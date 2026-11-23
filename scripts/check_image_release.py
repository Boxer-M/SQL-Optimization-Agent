"""Reject mismatched release tags before registry access."""
import os
from pathlib import Path
import re
import tomllib


def validate_version(version, ref):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Image releases require a stable X.Y.Z project version')
    if ref.startswith('refs/tags/') and ref != 'refs/tags/v' + version:
        raise ValueError('Git tag does not match pyproject.toml version')
    return version


if __name__ == '__main__':
    version = validate_version(tomllib.loads(Path('pyproject.toml').read_text())['project']['version'], os.environ.get('GITHUB_REF', ''))
    image = 'ghcr.io/' + os.environ['GITHUB_REPOSITORY'].lower()
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'version={version}\nimage={image}\n')
