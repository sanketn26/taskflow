import re

import taskwire


def test_version_is_set_and_pep440_shaped():
    assert taskwire.__version__
    assert re.match(r"^\d+\.\d+\.\d+", taskwire.__version__)
