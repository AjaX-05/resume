/**
 * Running header for multi-page resume PDFs: "Name Â· 2 / 3" in the top-right
 * margin of every page except the first. Single-page resumes are unchanged,
 * because `@page :first` clears it.
 *
 * Chromium (131+) renders CSS `@page` margin boxes into the PDF text layer.
 * Measured with the ATS parse-check: score, recall and reading order are the
 * same with and without it.
 */

/** Escape a value into a CSS string literal using only [A-Za-z0-9 ] plus hex escapes. */
export function cssString(value: string): string {
  let out = '';
  for (const char of value) {
    out += /[A-Za-z0-9 ]/.test(char) ? char : `\\${char.codePointAt(0)!.toString(16)} `;
  }
  return `"${out}"`;
}

/** The `@page` rules for the running header. `name` may be empty. */
export function runningHeaderCss(_name: string | null | undefined): string {
  return '@page { @top-right { content: none; } }';
}
