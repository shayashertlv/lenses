/**
 * A small schema-driven control panel.
 *
 * The fit parameters are the point of this build — anchor offsets, tilt, sizing
 * mode — and they need to be adjustable while watching a face, not recompiled
 * between guesses. Declaring them as data keeps adding a knob down to one entry.
 */

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

/**
 * Builds controls into `container`.
 *
 * `schema` is a list of groups, each `{ label, controls: [...] }`. Returns a live
 * `values` object plus helpers to react to and reflect external changes.
 */
export function createControls(container, schema, onChange) {
  const values = {};
  const nodes = new Map();

  const emit = (key) => onChange(key, values[key], values);

  for (const group of schema) {
    const section = el('section', 'group');
    section.append(el('h2', null, group.label));

    for (const control of group.controls) {
      const { key, type, label, hint } = control;
      const row = el('div', `row row--${type}`);
      row.dataset.key = key ?? '';

      if (type === 'button') {
        const button = el('button', 'button', label);
        button.type = 'button';
        button.addEventListener('click', control.onClick);
        row.append(button);
        section.append(row);
        nodes.set(key ?? label, { row, input: button });
        continue;
      }

      values[key] = control.value;

      const labelNode = el('label', 'row__label', label);
      labelNode.htmlFor = `ctl-${key}`;
      row.append(labelNode);

      let input;
      let readout;

      if (type === 'select') {
        input = el('select', 'select');
        for (const option of control.options) {
          const node = el('option', null, option.label ?? option);
          node.value = option.value ?? option;
          input.append(node);
        }
        input.value = control.value;
        input.addEventListener('change', () => {
          values[key] = input.value;
          emit(key);
        });
      } else if (type === 'toggle') {
        input = el('input');
        input.type = 'checkbox';
        input.className = 'toggle';
        input.checked = !!control.value;
        input.addEventListener('change', () => {
          values[key] = input.checked;
          emit(key);
        });
      } else if (type === 'range') {
        input = el('input');
        input.type = 'range';
        input.className = 'range';
        input.min = control.min;
        input.max = control.max;
        input.step = control.step ?? 0.01;
        input.value = control.value;
        readout = el('output', 'row__value', formatValue(control, control.value));
        input.addEventListener('input', () => {
          values[key] = parseFloat(input.value);
          readout.textContent = formatValue(control, values[key]);
          emit(key);
        });
      }

      input.id = `ctl-${key}`;
      if (hint) input.title = hint;
      row.append(input);
      if (readout) row.append(readout);
      section.append(row);
      nodes.set(key, { row, input, readout, control });
    }

    container.append(section);
  }

  return {
    values,

    /** Pushes a value in from outside without firing `onChange`. */
    set(key, value) {
      const entry = nodes.get(key);
      if (!entry) return;
      values[key] = value;
      if (entry.input.type === 'checkbox') entry.input.checked = !!value;
      else entry.input.value = value;
      if (entry.readout) entry.readout.textContent = formatValue(entry.control, value);
    },

    /** Shows or hides a control — used for knobs that only apply in one mode. */
    setVisible(key, visible) {
      const entry = nodes.get(key);
      if (entry) entry.row.hidden = !visible;
    },
  };
}

function formatValue(control, value) {
  const decimals = control.decimals ?? 2;
  return `${Number(value).toFixed(decimals)}${control.unit ?? ''}`;
}

/** The live numeric readouts above the controls. */
export function createReadout(container, fields) {
  const nodes = {};
  for (const [key, label] of Object.entries(fields)) {
    const cell = el('div', 'stat');
    cell.append(el('span', 'stat__label', label));
    const value = el('span', 'stat__value', '—');
    cell.append(value);
    container.append(cell);
    nodes[key] = value;
  }
  return {
    set(key, text) {
      if (nodes[key]) nodes[key].textContent = text;
    },
  };
}
