# Real Home Assistant, not stubs.
#
# Built on the official image so the integration is exercised against the exact
# Home Assistant and Python the add-on actually runs on. The harness pin below
# resolves to homeassistant==2026.8.2, which is what this image already ships,
# so installing it does not swap the version out from under the tests.
FROM ghcr.io/home-assistant/home-assistant:stable

# Keep this in step with the image: `pip index versions
# pytest-homeassistant-custom-component` lists which Home Assistant each release
# pins. A mismatch silently reinstalls Home Assistant and the run stops being a
# test of what ships.
ARG HARNESS_VERSION=0.13.356
RUN pip install --no-cache-dir \
      "pytest-homeassistant-custom-component==${HARNESS_VERSION}" \
    && python -c "from homeassistant.const import __version__ as v; print('home assistant', v)"

# The base image starts Home Assistant; here we only ever run pytest.
ENTRYPOINT []
WORKDIR /workspace
# asyncio_mode lives here rather than in pyproject.toml: pytest-asyncio is only
# installed in this image, and an unknown option would warn on every run of
# the fast suite outside it. The harness ships autouse async fixtures that
# strict mode refuses to run.
CMD ["pytest", "tests_integration", "-q", "-o", "asyncio_mode=auto"]
