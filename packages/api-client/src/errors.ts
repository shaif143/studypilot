/** Typed API failures, so the UI can explain what went wrong rather than "error". */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** The request needs consent before content may leave the device. */
  get isConsentRequired(): boolean {
    return this.status === 403 && this.code === 'consent_required';
  }

  /** The configured model provider is not usable right now. */
  get isProviderUnavailable(): boolean {
    return this.status === 503 || this.code === 'provider_unavailable';
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** The backend rejected the request body. */
  get isValidation(): boolean {
    return this.status === 422 || this.status === 400;
  }

  get isUnauthorised(): boolean {
    return this.status === 401;
  }
}

export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NetworkError';
  }
}
