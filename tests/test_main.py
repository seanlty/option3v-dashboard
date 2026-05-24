from src.main import main


def test_main_runs(capsys):
    main()
    captured = capsys.readouterr()
    assert "quant-assistant project is ready." in captured.out
