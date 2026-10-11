# World Prediction Engine v0.1

観測 → 変化検出 → 確率推定 → 台帳保存 → 結果採点を実行するPython研究用プロトタイプ。
Python 3.10以上、標準ライブラリのみ。GitHubへの公開は行っていません。

## すぐ動かす

ZIPを解凍して、このREADMEのあるフォルダで実行します。

```bash
python -m unittest discover -s tests -v
python -m wpe --db demo.sqlite demo
python -m wpe --db demo.sqlite report --output report.md
```

DEMOは固定乱数で作った1,200日分の合成価格。現実の世界情勢の推定値ではありません。
空のDBで一度実行してください。再実行時は別のDB名を指定します。
実行済みの出力は`examples/demo-result.json`と`examples/demo-report.md`にあります。

## 実データを取り込む

```bash
python -m wpe --db live.sqlite worldbank --country JPN --indicator FP.CPI.TOTL.ZG --start 1960 --end 2025
python -m wpe --db live.sqlite import-csv observations.csv
```

World Bank接続にはインターネットが必要。この作成環境では外部API実通信は未検証。
API形式とページングは公式資料に基づき実装し、モックで検証しました。
取得失敗はエラーになり、DEMOデータで代替しません。

CSV形式:

```csv
variable,region,observed_on,known_on,value,source,mode
cpi_yoy,JPN,2025-12-31,2026-01-23,2.1,official-vintage,live
```

`observed_on`は統計対象期間の終わり、`known_on`は値を確認できた日。
既存値は上書きせず、改訂値は新しい`known_on`で追加します。
日内の公表前後を区別できない日末の研究用。同日の複数改訂は保持できません。
複数の提供元は混ぜず、必要なら`--source`で選びます。単位は全期間で統一してください。

**現行World Bank APIの過去年次値は、当時の公表日時・全改訂履歴を提供しません。**
`known_on`を取得日に設定するため、今日取得した値だけでは過去時点の学習窓を作れず予測を拒否します。
ALFREDのvintageや当時公表されたCSV等の追加データが必要です。
年次指標だけで週次危機の変化は評価できません。

## 予測・検証

```bash
python -m wpe --db demo.sqlite forecast --variable synthetic_price --region DEMO --as-of 2012-06-19 --horizon 30 --threshold 90
python -m wpe --db demo.sqlite backtest --variable synthetic_price --region DEMO --as-of 2013-04-14 --horizon 30 --threshold 90
python -m wpe --db demo.sqlite resolve PREDICTION_ID --as-of 2013-04-14
```

`PREDICTION_ID`をforecastの出力IDへ置換します。
問題は「基準日の翌日から期限日までの観測値に、閾値を厳密に超える値が一つでもあるか」。
観測間の瞬間的超過は判定しません。負の結果は期限日に観測値がなければ未解決。

モデルはLaplace平滑化したベースレートと過去類似窓の発生率の固定50:50平均。
特徴量は直近12観測の値、後方移動平均の一次・二次差分、Zスコア。
差分は日数で割ります。三階差分・因果推論は未実装。
出力は未校正の研究用推定で、検証済みの危機発生確率ではありません。

期限が満了した20学習窓以上が必要。標準化は学習データだけで計算します。
各過去窓の特徴量はその時点のvintageから再構築。
バックテストは時間順、テスト窓は重複せず、当時のベースレートとBrierで比較します。
学習窓の重複は残るため独立標本数として数えられません。
判定ラベルは評価時点で得た改訂値に基づきます。調整するなら別の検証区間が必要。

## 含まれるもの・拡張

SQLiteスキーマ、CSV/World Bankアダプター、変化量・異常度、過去類似窓モデル、
予測台帳、初回判定とBrier、時系列バックテスト、校正ビン、Markdownレポート、QAテスト。
設計・データ源・運用方針は`docs/DESIGN.md`、類似研究は`docs/RELATED-WORK.md`。

常時稼働、週次自動更新、RSS/LLM、APIサーバー、PostgreSQL、因果グラフ、Monte Carlo、
日本への伝播モデルは未実装。以前設定された自動化も変更していません。
最初に公表時点の実データと採点可能な予測問題を揃え、検証結果で拡張の採否を決めます。
