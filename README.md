# World Prediction Engine v0.4

公開情報の収集、変化の計算、利害関係の分析、危機シナリオと早期警戒を扱う研究用プロトタイプです。
被害が起きる前に備えることを目的とし、予測の根拠、情報基準時刻、不確実性、結果評価を残します。
Python 3.10以上。実行時の外部パッケージは不要。AI APIキーも不要です。

## まず動かす（ネット接続不要）

ZIPを解凍し、`world-prediction-engine` フォルダで実行します。
Windowsで`python`が使えない場合は`py`、macOS/Linuxでは`python3`に読み替えてください。

```bash
python -m unittest discover -s tests -v
python -m wpe.risk_demo
```

`outputs/risk-demo/risk.html` をブラウザで開きます。
警戒条件、地域・期限、根拠、確率を算出できない理由、備えの負担、司法の記録が表示されます。
デモは**架空地域・架空資料**で、実際の犯罪リスクを表していません。
同じDBへの再実行は冪等です。新しいDBで比較するなら `--db data/demo2.sqlite` を指定します。
過去の評価日時へ戻す場合も別DBを使ってください。

## 機能

| 領域 | 実装した処理 |
|---|---|
| 情報収集 | RSS、World Bank、国会会議録の定期取得、取得ログ、原文の版管理。 |
| 数値処理 | 差分・加速度、累積超過、基準率と類似履歴による閾値予測、Brier評価。 |
| 利害分析 | 受益・負担・影響行使・事前準備を分けた台帳、条件付き行動候補。 |
| 危機シナリオ | 地域・被害種別・期限・必要条件・先例・反証・備えを保存。確率不明を許容。 |
| 早期警戒 | 指標を条件と比較、古い情報・欠測をunknownに、状態変化の通知をSQLiteに保存。 |
| 司法の記録 | 警察・検察・裁判所・監督機関の段階別記録。対応不全と腐敗の根拠を分離。 |
| 地域の指標 | 定義・単位・分母・取得時点を保持、人口10万人当たりの換算、微分・積分。 |
| 結果判定 | シナリオの期限後の結果を根拠付きで手動登録。負の判定には網羅性の宣言が必須。 |

LLMによる自動分析・校正済みの犯罪予測モデル・世界全域の網羅的収集は未実装です。
宗教・文化的な規範は根拠付きの条件や仮説として記録できます。
民族・宗教名から個人の犯罪性を採点する機能はありません。
報道量、認知件数、逮捕、有罪、被害調査は別の指標です。

## シナリオを登録する

`examples/risk-demo.json` が動く入力例です。実データはID・出典・原文・時刻・条件などを差し替え、別DBへ登録します。
ファイル中の内容を使う際は、架空デモと実データを混ぜないでください。

```bash
python -m wpe.risk --db data/risk-demo.sqlite import examples/risk-demo.json
python -m wpe.risk --db data/risk-demo.sqlite evaluate --as-of 2026-01-02T00:00:00Z
python -m wpe.risk --db data/risk-demo.sqlite report --as-of 2026-01-02T00:00:00Z --output outputs/risk.html
python -m wpe.risk --db data/risk-demo.sqlite export --as-of 2026-01-02T00:00:00Z --output outputs/risk.json
```

実データの評価では `--as-of` を省略すると現在のUTC時刻を使います。
過去日の指定は履歴の再現用です。`recorded_at`が別に残り、後から入力した過去の予測を前向き実績とみなしません。

警戒状態は `watch / not_triggered / unknown / refuted / expired` の5種類。
すべての警戒指標成立でwatch、どれかの反証成立でrefuted、必要な情報が欠ければunknownです。
警戒未成立・期限切れは安全や「被害なし」を意味しません。
同じ状態・シナリオ版・根拠の組み合わせは繰り返し通知しません。状態が戻った後の再警戒は通知します。
通知はローカルDBに保存するだけで、メール・SNSへの送信はありません。

### 地域の数値を見る

```bash
python -m wpe.regional --db data/risk-demo.sqlite --metric equipment_reports --region DEMO --as-of 2026-01-03T00:00:00Z --per-capita --baseline 30
```

人口10万人当たりの申告数、変化速度、基準超過の面積を出します。
分母は登録者が指定した値です。住民人口・滞在人口などの定義を`population`に記録してください。
比較不能な期間・欠測を無理に補間しません。積分値は被害者数や事件数そのものではありません。

### シナリオの結果を残す

```bash
python -m wpe.risk --db data/risk-demo.sqlite resolve examples/risk-resolution.json
```

例は「結果不明」。`occurred`はtrue/false/nullです。falseの場合は、根拠と`coverage_complete: true`が必要。
網羅性は登録者の宣言であり、自動検証ではありません。シナリオは確率予測ではないためBrier採点しません。
修正はシナリオの新しい連番revisionや新しい証拠IDとして登録し、過去を上書きしません。
詳細は [入力仕様](docs/RISK-DATA.md)。

## 既存の利害分析・数値予測

```bash
python -m wpe.actor_demo
python -m wpe --db data/numeric-demo.sqlite demo
```

利害分析の登録・手動確定手順は [v0.3の説明](README-v0.3.md)。
自動収集と時系列処理は [v0.2の説明](README-v0.2.md)。
架空デモのDBは実運用と分離してください。

## 定期収集

```bash
python -m wpe.autonomous once
python -m wpe.autonomous loop
```

`config/autonomous.json`の取得間隔・対象を確認してください。
`outputs/latest.json`、`actors.html`、`risk.html`を更新します。
シナリオと指標は`wpe.risk import`で同じ`data/runtime.sqlite`へ事前登録します。
犯罪統計や司法記録からrisk入力への自動変換は未実装。RSSの記事件数を犯罪件数へ変換しません。
外部接続はこの環境で実通信を検証していません。取得失敗時はdegradedになり、欠測をゼロにしません。
ループ停止はCtrl+C。Docker用ファイルは同梱していますがDocker運転は未検証です。

## GitHubへ上げる

この一式はGit用に整えたソースです。GitHubのリポジトリ作成・pushはまだ行っていません。
手順は [GitHubへのアップロード](docs/GITHUB-SETUP.md)。
`.gitignore`でDB、収集データ、出力、環境変数を除外。公開データは架空例のみです。
GitHub ActionsでPython 3.10/3.12/3.13のテストとデモを実行する設定を同梱しています。
Actionsはリモート実行前のため、ローカル検証と区別してください。

## 設計と検証

- [統合要求仕様書](docs/INTEGRATED-SPECIFICATION.md)
- [要求と実装の対応](docs/IMPLEMENTATION-STATUS.md)
- [v0.4検証記録](docs/VALIDATION-v0.4.md)
- [入力仕様](docs/RISK-DATA.md)

自動の警戒は登録された条件の照合であり、犯罪の発生確率、個人の有罪、宗教・民族全体の性質を認定しません。
実運用での予測精度は未実証。READMEと検証記録に示した範囲が、この版の到達点です。
ライセンスの付与は未設定です。公開先と配布条件は所有者が決めてください。
