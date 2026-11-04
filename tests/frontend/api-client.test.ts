/**
 * The HTTP boundary: authentication, error translation and scoping.
 *
 * These matter because every screen depends on them: a mistranslated error is
 * shown to a student as "something went wrong", and a dropped token header
 * breaks the whole desktop app.
 */
import { ApiError, NetworkError, StudyPilotClient } from '@studypilot/api-client';
import type { Platform } from '@studypilot/shared';
import { beforeEach, describe, expect, it, vi } from 'vitest';

function platform(overrides: Partial<Platform> = {}): Platform {
  return {
    kind: 'desktop',
    apiBaseUrl: 'http://127.0.0.1:9999',
    hasSecureStorage: true,
    getApiToken: async () => 'secret-token',
    openExternal: async () => {},
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

describe('StudyPilotClient', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  it('attaches the token header to every request', async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    const client = new StudyPilotClient(platform());

    await client.courses();

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('http://127.0.0.1:9999/api/courses');
    expect((init.headers as Record<string, string>)['x-studypilot-token']).toBe('secret-token');
  });

  it('resolves the token once and reuses it', async () => {
    const getApiToken = vi.fn(async () => 'secret-token');
    // A Response body can only be read once, so each call needs a fresh one.
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse([])));
    const client = new StudyPilotClient(platform({ getApiToken }));

    await client.courses();
    await client.documents();

    expect(getApiToken).toHaveBeenCalledTimes(1);
  });

  it('omits the token when the host supplies none', async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    const client = new StudyPilotClient(
      platform({ kind: 'web', apiBaseUrl: '', getApiToken: async () => null }),
    );

    await client.courses();

    const init = fetchMock.mock.calls[0]![1];
    expect((init.headers as Record<string, string>)['x-studypilot-token']).toBeUndefined();
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/courses');
  });

  it('drops the cached token after a 401 so a restarted backend recovers', async () => {
    const getApiToken = vi.fn(async () => 'secret-token');
    const client = new StudyPilotClient(platform({ getApiToken }));

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'Invalid token.' }, 401));
    await expect(client.courses()).rejects.toMatchObject({ status: 401 });

    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await client.courses();

    expect(getApiToken).toHaveBeenCalledTimes(2);
  });

  it('serialises query parameters and skips empty ones', async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    const client = new StudyPilotClient(platform());

    await client.flashcards({ course_id: 'abc', due_only: true, limit: undefined });

    expect(fetchMock.mock.calls[0]![0]).toBe(
      'http://127.0.0.1:9999/api/flashcards?course_id=abc&due_only=true',
    );
  });

  it('translates a consent refusal into a typed error', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { detail: 'That would send your course material off this device.', code: 'consent_required' },
        403,
      ),
    );
    const client = new StudyPilotClient(platform());

    const error = await client.ask({ question: 'hi' }).catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).isConsentRequired).toBe(true);
    expect((error as ApiError).message).toContain('off this device');
  });

  it('flattens FastAPI validation errors into one readable sentence', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          detail: [
            { loc: ['body', 'question_count'], msg: 'Input should be less than or equal to 40' },
            { loc: ['body', 'course_id'], msg: 'Field required' },
          ],
        },
        422,
      ),
    );
    const client = new StudyPilotClient(platform());

    const error = (await client
      .generateAssessment({ course_id: '' })
      .catch((cause: unknown) => cause)) as ApiError;

    expect(error.isValidation).toBe(true);
    expect(error.message).toContain('question_count: Input should be less than or equal to 40');
    expect(error.message).toContain('course_id: Field required');
  });

  it('reports an unreachable backend as a network error, not a generic one', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const client = new StudyPilotClient(platform());

    const error = await client.health().catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(NetworkError);
    expect((error as NetworkError).message).toContain('Could not reach');
  });

  it('propagates an abort rather than reporting it as a failure', async () => {
    fetchMock.mockRejectedValue(new DOMException('aborted', 'AbortError'));
    const client = new StudyPilotClient(platform());

    const error = await client.health().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(DOMException);
    expect((error as DOMException).name).toBe('AbortError');
  });

  it('returns undefined for a 204 instead of failing to parse', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const client = new StudyPilotClient(platform());
    await expect(client.deleteCourse('abc')).resolves.toBeUndefined();
  });

  it('sends an upload as multipart without a JSON content type', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ document: {} }));
    const client = new StudyPilotClient(platform());
    const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'lecture.pdf', {
      type: 'application/pdf',
    });

    await client.uploadDocument('course-1', file, { title: 'Lecture 7' });

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toContain('/api/courses/course-1/documents');
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Record<string, string>)['content-type']).toBeUndefined();
    expect((init.body as FormData).get('title')).toBe('Lecture 7');
  });

  it('builds binary URLs against the configured base', () => {
    const client = new StudyPilotClient(platform());
    expect(client.documentFileUrl('doc-1')).toBe('http://127.0.0.1:9999/api/documents/doc-1/file');
    expect(client.pagePreviewUrl('doc-1', 4)).toBe(
      'http://127.0.0.1:9999/api/documents/doc-1/pages/4/preview',
    );
  });

  it('falls back to a status message when the error body is not JSON', async () => {
    fetchMock.mockResolvedValue(new Response('<html>502</html>', { status: 502 }));
    const client = new StudyPilotClient(platform());

    const error = (await client.health().catch((cause: unknown) => cause)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toContain('502');
  });

  // --- the sign-in gate reads this, so a wrong answer here opens the app ---

  it('treats a missing auth route on the desktop as "no accounts here"', async () => {
    // The desktop backend does not mount the account routes at all, so a 404
    // really does mean the local profile is the only user.
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Not Found' }, 404));
    const client = new StudyPilotClient(platform({ kind: 'desktop' }));

    const status = await client.authStatus();

    expect(status.requires_authentication).toBe(false);
    expect(status.authenticated).toBe(true);
  });

  it('fails closed when the auth route is missing on the web', async () => {
    // On a website a 404 means the API is missing or misrouted. Answering
    // "nobody needs to sign in" would hand the whole interface to a visitor,
    // which is what happened on the first deployment with no API behind it.
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Not Found' }, 404));
    const client = new StudyPilotClient(platform({ kind: 'web', apiBaseUrl: '' }));

    const error = (await client.authStatus().catch((cause: unknown) => cause)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
  });

  it('attaches a fresh bearer token to every request', async () => {
    // Neon Auth tokens expire after 15 minutes, so caching one would start
    // failing silently a quarter of an hour in.
    const getBearerToken = vi.fn(async () => 'neon-jwt');
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse([])));
    const client = new StudyPilotClient(
      platform({ kind: 'web', apiBaseUrl: '', getApiToken: async () => null, getBearerToken }),
    );

    await client.courses();
    await client.documents();

    expect(getBearerToken).toHaveBeenCalledTimes(2);
    for (const call of fetchMock.mock.calls) {
      expect((call[1].headers as Record<string, string>).authorization).toBe('Bearer neon-jwt');
    }
  });

  it('sends no authorization header when the bearer cannot be obtained', async () => {
    // Nobody is signed in, or Neon is unreachable. The backend answers 401
    // and the gate shows its sign-in screen, which is the right outcome.
    fetchMock.mockResolvedValue(jsonResponse([]));
    const client = new StudyPilotClient(
      platform({
        kind: 'web',
        apiBaseUrl: '',
        getApiToken: async () => null,
        getBearerToken: async () => {
          throw new Error('not signed in');
        },
      }),
    );

    await client.courses();

    const init = fetchMock.mock.calls[0]![1];
    expect((init.headers as Record<string, string>).authorization).toBeUndefined();
  });
});
