/** Keep the existing AR controls and handlers; only their local studio presentation is compacted. */
export function installStudioLayout(): void {
  document.documentElement.classList.add('studio-embedded');
  const sidebar = document.querySelector<HTMLElement>('.sidebar');
  if (sidebar && !sidebar.querySelector('.studio-options')) {
    const details = document.createElement('details'), summary = document.createElement('summary'), content = document.createElement('div');
    details.className = 'studio-options'; summary.textContent = 'Fit, hair and shadows'; content.className = 'studio-options-content';
    content.append(...Array.from(sidebar.children)); details.append(summary, content); sidebar.append(details);
  }
  const title = document.querySelector('#welcome h2'), description = document.querySelector('#welcome-description'), guidance = document.querySelector('#guidance');
  if (title) title.textContent = 'Try on this model';
  if (description) description.textContent = 'Open the camera, then look straight ahead to settle the fit.';
  if (guidance) guidance.textContent = 'Open the camera to preview this model.';
}
