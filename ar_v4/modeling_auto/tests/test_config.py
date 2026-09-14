"""Settings loaded after restart must keep this app's saved key correction."""
import pytest

from app import config


def test_saved_keys_win_over_stale_inherited_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'old-inherited-openai')
    monkeypatch.setenv('MESHY_API_KEY', 'old-inherited-meshy')
    (tmp_path / '.env').write_text(
        '\ufeffOPENAI_API_KEY="sk-saved-openai"\nMESHY_API_KEY=saved-meshy\n', encoding='utf-8')
    assert config.local_keys() == {'OPENAI_API_KEY': 'sk-saved-openai', 'MESHY_API_KEY': 'saved-meshy'}
    assert config.Settings.load().openai_key == 'sk-saved-openai'


@pytest.mark.parametrize('contents', [None, '# No local OpenAI key\nMESHY_API_KEY=local-meshy\n'])
def test_environment_is_fallback_only_for_absent_local_setting(tmp_path, monkeypatch, contents):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-environment-openai')
    monkeypatch.delenv('MESHY_API_KEY', raising=False)
    if contents is not None:
        (tmp_path / '.env').write_text(contents, encoding='utf-8')
    assert config.local_keys() == {
        'OPENAI_API_KEY': 'sk-environment-openai',
        'MESHY_API_KEY': 'local-meshy' if contents is not None else '',
    }


def test_explicit_empty_local_key_does_not_activate_inherited_account(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-inherited-openai')
    (tmp_path / '.env').write_text('OPENAI_API_KEY=\n', encoding='utf-8')
    assert config.local_keys()['OPENAI_API_KEY'] == ''
