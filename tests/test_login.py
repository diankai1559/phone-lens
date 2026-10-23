from phonelens import bili_login


def test_save_and_load_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(bili_login, "CREDENTIAL", tmp_path / "credential.json")
    monkeypatch.setattr(bili_login, "APP_DIR", tmp_path)

    bili_login.save({"SESSDATA": "abc123", "bili_jct": "jct", "DedeUserID": "42"})

    data = bili_login.load()
    assert data["sessdata"] == "abc123"
    assert data["dedeuserid"] == "42"
    assert bili_login.logged_in()


def test_svg_is_data_uri():
    uri = bili_login.svg("https://passport.bilibili.com/h5-app/passport/login/scan")
    assert uri.startswith("data:image/svg+xml;base64,")
    assert len(uri) > 1000
