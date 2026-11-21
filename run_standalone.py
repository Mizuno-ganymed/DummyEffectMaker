# -*- coding: utf-8 -*-
"""Maya 外でランチャーを起動する (UI 確認用 / 将来のスタンドアロン版の入口)。

    python run_standalone.py

Maya 専用ツールのボタンは無効化された状態で表示される。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dummy_effect_maker.ui.qt import QtWidgets  # noqa: E402


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    import dummy_effect_maker
    dummy_effect_maker.show()
    run = getattr(app, "exec", None) or app.exec_
    return run()


if __name__ == "__main__":
    sys.exit(main())
