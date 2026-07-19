from streamlit.testing.v1 import AppTest


def test_automation_modes_render_without_secrets():
    app = AppTest.from_file("streamlit_app.py").run(timeout=30)
    assert not app.exception
    assert app.radio[0].value == "google_sheets"

    app.radio[0].set_value("api").run(timeout=30)
    assert not app.exception
    assert app.selectbox[0].value == "ga4"

    app.radio[0].set_value("csv").run(timeout=30)
    assert not app.exception
    assert any(field.value == "performance_input" for field in app.text_input)

