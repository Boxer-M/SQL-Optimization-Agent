import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('check_image_release', Path(__file__).parents[1] / 'scripts/check_image_release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_release_tag_matches_version():
    assert module.validate_version('0.2.0', 'refs/tags/v0.2.0') == '0.2.0'
    assert module.validate_version('0.2.0', 'refs/pull/1/merge') == '0.2.0'


@pytest.mark.parametrize('version,ref', [('0.2.0', 'refs/tags/v0.3.0'), ('0.2.0', 'refs/tags/0.2.0'), ('0.2.0rc1', 'refs/tags/v0.2.0rc1')])
def test_release_rejects_invalid_version_or_tag(version, ref):
    with pytest.raises(ValueError):
        module.validate_version(version, ref)
