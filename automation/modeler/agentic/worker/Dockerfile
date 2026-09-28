# The isolated Linux Blender worker image for modeler.agentic (built locally; never pulled at job time).
#
#   docker build -t lenses-agentic-worker:blender-5.2 --build-arg BLENDER_SHA256=<verified> -f modeler/agentic/worker/Dockerfile modeler/agentic/worker
#   docker image inspect lenses-agentic-worker:blender-5.2 --format '{{.Id}}'      # sha256:<64 hex>, the content-addressed image ID
#
# Write that ID verbatim as image_digest in worker-config.json (a locally built image has no registry digest; docker resolves
# <name>@sha256:... only for pulled/pushed images) and run `python -m modeler.agentic doctor --worker-config ... --self-test`.
# Pin BLENDER_URL/BLENDER_SHA256 to the exact Linux build you verified (the audited host build is Blender 5.2.0 LTS); the
# checksum is enforced at build time. The image carries no credentials and no repository.
# The base by digest (the bookworm-slim this image was built from on 2026-09-28): a rebuild cannot drift to a newer base.
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251

ARG BLENDER_URL=https://download.blender.org/release/Blender5.2/blender-5.2.0-linux-x64.tar.xz
ARG BLENDER_SHA256=REPLACE_WITH_THE_VERIFIED_SHA256_OF_THE_TARBALL

# libgl1-mesa-dri / libglx-mesa0: Mesa's software rasterizer (llvmpipe) so EEVEE can create a GL context and render headless
# without a GPU (unverified on this machine: no engine; the doctor self-test's render step is where it would show)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates xz-utils python3 libgl1 libgl1-mesa-dri libglx-mesa0 libxi6 libxrender1 libxkbcommon0 libsm6 libxxf86vm1 libxfixes3 libegl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p /opt/blender /opt/lenses; \
    curl_or_wget() { if command -v curl >/dev/null; then curl -fsSL "$1" -o "$2"; else wget -q "$1" -O "$2"; fi; }; \
    apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*; \
    curl_or_wget "$BLENDER_URL" /tmp/blender.tar.xz; \
    echo "$BLENDER_SHA256  /tmp/blender.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1; \
    rm /tmp/blender.tar.xz; \
    ln -s /opt/blender/blender /usr/local/bin/blender; \
    apt-get purge -y curl && apt-get autoremove -y

# Fonts for the library's text styles: glasses_lib maps 'sans', 'serif' and 'mono' to the first existing file of an ordered
# candidate list, these worker paths first (DejaVu Sans / Serif / Sans Mono, Liberation Sans / Serif). Until 2026-09-28 the image
# had no fonts, so every text mesh (lens print, tip inscriptions) fell back to Blender's built-in font (test-pilot-002, review
# MVP-10 / INF-09). The build fails when one of the files is missing (a renamed package must not fall back silently), and the
# font bytes and package versions are recorded in /opt/lenses/fonts.sha256, which the supervisor copies into every receipt.
# The package versions are pinned (bookworm's, 2026-09-28). A layer of its own after the Blender layer: adding it never
# re-downloads Blender.
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core=2.37-6 fonts-liberation=1:1.07.4-11 && rm -rf /var/lib/apt/lists/*; \
    test -s /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf; \
    test -s /usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf; \
    test -s /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf; \
    test -s /usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf; \
    test -s /usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf; \
    { dpkg-query -W -f='# ${Package} ${Version}\n' fonts-dejavu-core fonts-liberation; \
      find /usr/share/fonts -type f -name '*.ttf' | sort | xargs sha256sum; } > /opt/lenses/fonts.sha256; \
    chmod 0444 /opt/lenses/fonts.sha256

COPY entry.py /opt/lenses/worker_entry.py

# nonroot; the job also passes --user, --read-only, --cap-drop ALL, no-new-privileges, --network none and a bounded tmpfs at /work
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin lenses \
    && chmod 0555 /opt/lenses/worker_entry.py
USER 10001:10001
ENV HOME=/work/home TMPDIR=/work/tmp XDG_CACHE_HOME=/work/cache XDG_CONFIG_HOME=/work/config BLENDER_USER_RESOURCES=/work/blender PYTHONDONTWRITEBYTECODE=1
# The host always passes --entrypoint explicitly (python3 for jobs, blender for the doctor's version probe) and puts only the
# supervisor's arguments after the image; entry.py refuses any argv other than [<...>/operation.json, /work/out]. Tests parse
# this instruction (tests/test_agentic_executor.py, TestDockerEntrypointSemantics): keep the exec form.
ENTRYPOINT ["python3", "/opt/lenses/worker_entry.py"]
