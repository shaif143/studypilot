/**
 * A tiny arithmetic language for ```explore blocks.
 *
 * The expression is a model's text, so it is parsed here into a closed tree
 * of numbers, named variables and a fixed list of mathematical functions, and
 * evaluated by walking that tree. Nothing is ever handed to the JavaScript
 * engine as code: there is no `eval`, no `Function`, no property access, and
 * a name that is not a declared variable, a constant or one of the functions
 * below makes the whole expression invalid.
 */

type Scope = Record<string, number>;
export type Compiled = (scope: Scope) => number;

const CONSTANTS: Record<string, number> = { pi: Math.PI, e: Math.E };

const sinc = (x: number) => (x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x));

const FUNCTIONS: Record<string, { arity: number | 'many'; apply: (...args: number[]) => number }> =
  {
    sin: { arity: 1, apply: Math.sin },
    cos: { arity: 1, apply: Math.cos },
    tan: { arity: 1, apply: Math.tan },
    asin: { arity: 1, apply: Math.asin },
    acos: { arity: 1, apply: Math.acos },
    atan: { arity: 1, apply: Math.atan },
    sinh: { arity: 1, apply: Math.sinh },
    cosh: { arity: 1, apply: Math.cosh },
    tanh: { arity: 1, apply: Math.tanh },
    exp: { arity: 1, apply: Math.exp },
    ln: { arity: 1, apply: Math.log },
    log: { arity: 1, apply: Math.log },
    log10: { arity: 1, apply: Math.log10 },
    log2: { arity: 1, apply: Math.log2 },
    sqrt: { arity: 1, apply: Math.sqrt },
    abs: { arity: 1, apply: Math.abs },
    floor: { arity: 1, apply: Math.floor },
    ceil: { arity: 1, apply: Math.ceil },
    round: { arity: 1, apply: Math.round },
    sign: { arity: 1, apply: Math.sign },
    sinc: { arity: 1, apply: sinc },
    rect: { arity: 1, apply: (x) => (Math.abs(x) <= 0.5 ? 1 : 0) },
    step: { arity: 1, apply: (x) => (x >= 0 ? 1 : 0) },
    pow: { arity: 2, apply: Math.pow },
    mod: { arity: 2, apply: (a, b) => a - b * Math.floor(a / b) },
    min: { arity: 'many', apply: Math.min },
    max: { arity: 'many', apply: Math.max },
  };

export const FUNCTION_NAMES = new Set(Object.keys(FUNCTIONS));

type Token =
  | { type: 'number'; value: number }
  | { type: 'name'; value: string }
  | { type: 'op'; value: string };

const MAX_LENGTH = 300;
const MAX_DEPTH = 40;

function tokenise(source: string): Token[] | null {
  const tokens: Token[] = [];
  let index = 0;
  while (index < source.length) {
    const char = source[index] as string;
    if (char === ' ' || char === '\t' || char === '\n') {
      index += 1;
    } else if (/[0-9.]/.test(char)) {
      const match = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(source.slice(index));
      if (!match) return null;
      tokens.push({ type: 'number', value: Number(match[0]) });
      index += match[0].length;
    } else if (/[A-Za-z_]/.test(char)) {
      const match = /^[A-Za-z_][A-Za-z0-9_]*/.exec(source.slice(index));
      if (!match) return null;
      tokens.push({ type: 'name', value: match[0] });
      index += match[0].length;
    } else if ('+-*/^(),'.includes(char)) {
      tokens.push({ type: 'op', value: char });
      index += 1;
    } else {
      return null;
    }
  }
  return tokens;
}

/**
 * Compile `source` to a function of its variables, or null if it is not a
 * valid expression over exactly `variables`, the constants and the functions.
 */
export function compileExpression(source: string, variables: readonly string[]): Compiled | null {
  if (typeof source !== 'string' || source.length === 0 || source.length > MAX_LENGTH) return null;
  const scanned = tokenise(source);
  if (!scanned || scanned.length === 0) return null;
  const tokens: Token[] = scanned;
  const allowed = new Set(variables);
  let position = 0;
  let failed = false;

  const peek = () => tokens[position];
  const isOp = (value: string) => {
    const token = peek();
    return token !== undefined && token.type === 'op' && token.value === value;
  };
  const fail = (): Compiled => {
    failed = true;
    return () => Number.NaN;
  };

  function expression(depth: number): Compiled {
    if (depth > MAX_DEPTH) return fail();
    let left = term(depth + 1);
    while (isOp('+') || isOp('-')) {
      const op = (tokens[position] as { value: string }).value;
      position += 1;
      const right = term(depth + 1);
      const a = left;
      left = op === '+' ? (s) => a(s) + right(s) : (s) => a(s) - right(s);
    }
    return left;
  }

  function term(depth: number): Compiled {
    let left = unary(depth + 1);
    while (isOp('*') || isOp('/')) {
      const op = (tokens[position] as { value: string }).value;
      position += 1;
      const right = unary(depth + 1);
      const a = left;
      left = op === '*' ? (s) => a(s) * right(s) : (s) => a(s) / right(s);
    }
    return left;
  }

  function unary(depth: number): Compiled {
    if (depth > MAX_DEPTH) return fail();
    if (isOp('-')) {
      position += 1;
      const operand = unary(depth + 1);
      return (s) => -operand(s);
    }
    if (isOp('+')) {
      position += 1;
      return unary(depth + 1);
    }
    return power(depth + 1);
  }

  function power(depth: number): Compiled {
    const base = primary(depth + 1);
    if (isOp('^')) {
      position += 1;
      // Right-associative, and the exponent may carry its own sign: 2^-x.
      const exponent = unary(depth + 1);
      return (s) => base(s) ** exponent(s);
    }
    return base;
  }

  function primary(depth: number): Compiled {
    if (depth > MAX_DEPTH) return fail();
    const token = peek();
    if (!token) return fail();
    if (token.type === 'number') {
      position += 1;
      const value = token.value;
      return () => value;
    }
    if (token.type === 'name') {
      position += 1;
      const name = token.value;
      if (isOp('(')) {
        const fn = Object.hasOwn(FUNCTIONS, name) ? FUNCTIONS[name] : undefined;
        if (!fn) return fail();
        position += 1;
        const args: Compiled[] = [];
        if (!isOp(')')) {
          args.push(expression(depth + 1));
          while (isOp(',')) {
            position += 1;
            args.push(expression(depth + 1));
          }
        }
        if (!isOp(')')) return fail();
        position += 1;
        if (fn.arity === 'many' ? args.length < 1 || args.length > 6 : args.length !== fn.arity) {
          return fail();
        }
        return (s) => fn.apply(...args.map((arg) => arg(s)));
      }
      if (allowed.has(name)) return (s) => s[name] ?? Number.NaN;
      if (Object.hasOwn(CONSTANTS, name)) {
        const value = CONSTANTS[name] as number;
        return () => value;
      }
      return fail();
    }
    if (token.type === 'op' && token.value === '(') {
      position += 1;
      const inner = expression(depth + 1);
      if (!isOp(')')) return fail();
      position += 1;
      return inner;
    }
    return fail();
  }

  const compiled = expression(0);
  if (failed || position !== tokens.length) return null;
  return compiled;
}
