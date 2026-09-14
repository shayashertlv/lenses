import fs from 'node:fs/promises';
let html=await fs.readFile('experiments/speed-lab/live.html','utf8');
html=html.replace('<title>Lenses · G Combined</title>','<title>Lenses · FPS comparison</title>');
html=html.replace('SPEED LAB','FPS TEST');
html=html.replace('Switch algorithms live, then hold one image to inspect the details.','Compare G with two focused speed changes, at the same resolution.');
html=html.replace(/<div class="preview-notice">.*?<\/div>/,'<div class="preview-notice"><strong id="fps-mode-label">G · Current baseline</strong> · Separate performance test. G remains the accepted version. Changing the test starts a fresh camera session.</div>');
html=html.replace('<aside class="sidebar">',`<aside class="sidebar">
      <section class="pipeline-controls"><label class="eyebrow" for="fps-mode">PERFORMANCE TEST</label>
        <select id="fps-mode"><option value="g">G · Current baseline</option><option value="queries">Test 1 · Fewer graphics queries</option><option value="identity">Test 2 · Live frame IDs</option><option value="combined">Test 3 · Both changes</option></select>
        <p class="control-hint" id="fps-mode-description"></p>
        <button id="fps-study-start" class="primary">Compare G / test / test / G</button>
        <button id="fps-study-cancel" class="quiet" hidden>Stop comparison</button>
        <p id="fps-study-status" class="control-hint" role="status">Choose a test, then compare while moving down, up and both ways.</p>
        <div id="fps-study-results" class="control-hint"></div>
        <button id="fps-study-download" class="quiet" hidden>Download comparison</button>
      </section>`);
html=html.replace('id="stage-toggle-pipeline"','hidden id="stage-toggle-pipeline"');
html=html.replace('<section class="pipeline-controls"><label class="eyebrow" for="pipeline-select">','<section class="pipeline-controls" hidden><label class="eyebrow" for="pipeline-select">');
html=html.replace('<label class="control-hint" for="benchmark-order">','<div hidden><label class="control-hint" for="benchmark-order">');
html=html.replace('<button id="download-metrics"','</div><button id="download-metrics"');
html=html.replace('src="./live-main.ts"','src="./entry.ts"');
html=html.replace('Hold a frame to compare all eight on exactly the same image.','Hold stops the camera and preserves this exact image for inspection.');
html=html.replace('Update rate and hair coverage reset after an algorithm switch.','Update rate and hair coverage restart in each test session.');
html=html.replace(/<div class="baseline-note">.*?<\/div>/,'<div class="baseline-note"><span class="pill">G remains accepted</span><p>Both frames and hair models are available. Resolution, image/pose/mask pairing and final nose/front protection stay active. A higher FPS number needs matching hair coverage and your visual review.</p></div>');
html=html.replace('</head>','<style>[hidden]{display:none!important} #fps-study-results{line-height:1.7} #fps-mode{width:100%}</style></head>');
await fs.writeFile('experiments/fps-candidate/live.html',html,{flag:'wx'});
