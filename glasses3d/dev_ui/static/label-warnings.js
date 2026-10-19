// Keep the warning rule aligned with dev_ui/labels.py. Owner text is never changed.
// Python's Unicode \w is a letter, number or underscore (not a combining mark).
const WORD_CHARACTER = /[\p{L}\p{N}_]/u;
// Match str.strip()'s whitespace set, including its four information separators.
const BLANK_TEXT = /^[\p{White_Space}\u001c-\u001f]*$/u;

function standaloneMatches(text, expression) {
  const matches = [];
  for (const match of text.matchAll(expression)) {
    // Two UTF-16 units include a neighbouring supplementary Unicode character.
    const end = match.index + match[0].length;
    const before = [...text.slice(Math.max(0, match.index - 2), match.index)].at(-1) || '';
    const after = [...text.slice(end, end + 2)][0] || '';
    if (!WORD_CHARACTER.test(before) && !WORD_CHARACTER.test(after)) matches.push(match[0]);
  }
  return matches;
}

function mentions(text, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return standaloneMatches(text, new RegExp(escaped, 'gu')).length > 0;
}

/**
 * Return warnings for one item's words, without modifying them.
 * sessionLabels contains every label available to these words. itemLabels selects
 * the marks to check for unused labels; pass sessionLabels to check all marks.
 * otherTexts contains the other items' words, since any item can cite any mark.
 */
export function labelWarnings(text, sessionLabels, itemLabels = [], otherTexts = []) {
  const words = typeof text === 'string' ? text : '';
  const known = new Set(sessionLabels);
  const warnings = [];
  if (BLANK_TEXT.test(words)) {
    warnings.push({ code: 'empty_text', label: '', message: 'Describe what you want to change in this item.' });
  }
  for (const label of [...new Set(standaloneMatches(words, /[A-Z][0-9]*/gu))].sort()) {
    if (label !== 'A' && label !== 'I' && !known.has(label)) {
      warnings.push({ code: 'unknown_label', label, message: `Label "${label}" does not exist in this edit session.` });
    }
  }
  const allWords = [words, ...otherTexts.filter(value => typeof value === 'string')];
  for (const label of new Set(itemLabels)) {
    if (typeof label === 'string' && label && !allWords.some(value => mentions(value, label))) {
      warnings.push({ code: 'unused_label', label, message: `Label "${label}" is not mentioned in any edit request.` });
    }
  }
  return warnings;
}
