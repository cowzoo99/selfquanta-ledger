import re
import unittest
from pathlib import Path


def test_app_metadata_is_importable():
    import internal_ledger

    assert internal_ledger.__version__ == "0.1.0"


def test_member_page_is_admin_only_in_the_workbench_source():
    root = Path(__file__).parents[1] / "public" / "frontend" / "src"
    app = (root / "App.vue").read_text(encoding="utf-8")
    api = (root / "api.js").read_text(encoding="utf-8")
    assert (root / "pages" / "EnterpriseMembers.vue").exists()
    assert "import EnterpriseMembers from './pages/EnterpriseMembers.vue'" in app
    assert re.search(r"canWrite\s*\?\s*\[\['members',\s*'企业成员'\]\]\s*:\s*\[\]", app)
    assert 'v-for="entry in navigation"' in app
    assert re.search(r'<EnterpriseMembers\s+v-else-if="page === \'members\' && canWrite"', app)
    assert re.search(r"const page = computed\(\(\) => navigation.some\(entry => entry\[0\] === props.view\) \|\| \['duplicates', 'classification'\].includes\(props.view\) \? props.view : 'dashboard'\)", app)
    assert "export async function memberCall(method, args = {})" in api
    assert "internal_ledger.api.member_api.${method}" in api
    members = (root / "pages" / "EnterpriseMembers.vue").read_text(encoding="utf-8")
    assert "建议至少 12 位" in members
    assert "大写字母、小写字母、数字和特殊符号" in members
    assert 'aria-describedby="member-password-help"' in members
    assert 'aria-describedby="reset-password-help"' in members


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([
        unittest.FunctionTestCase(test_app_metadata_is_importable),
        unittest.FunctionTestCase(test_member_page_is_admin_only_in_the_workbench_source),
    ])
