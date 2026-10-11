# World Prediction Engine v0.3 — 出来事・利害・行動予測

v0.2の定期収集と計算に、原文の証拠、制度変更への言及候補、関係者の利害、行動予測の台帳を追加しました。
Python 3.10以上。標準ライブラリのみ。GitHubへ公開・外部ホストへの配置はまだ行っていません。

## 最初に動作例を見る

```bash
python -m unittest discover -s tests -v
python -m wpe.actor_demo
```

`outputs/actor-demo/actors.html`をブラウザで開くと、出来事候補、受益仮説、行動候補、期限付き予測、原文、Brier採点が見られます。
全て架空のデータ。デモは新しいDBで実行してください。再実行する場合は`--db data/actor-demo2.sqlite`等で別名を指定。
実行済みの例は`examples/actor-demo/actors.html`と`actors.json`。現実の制度・企業・確率ではありません。
デモDBを実データのDBとして使わないでください。

## 自動収集する

```bash
python -m wpe.autonomous once
python -m wpe.autonomous loop
```

config/autonomous.jsonにFRB・BISのRSS、World Bank3系列、国会会議録の「補助金」検索を設定。
国会会議録は直近30日の検索結果の最初の100発言だけ取得します。上限超過を記録し、完全収集とは表示しません。
1日1回の1リクエストで、大量取得・並列取得はしません。追加する場合はAPI公式利用条件に沿って間隔を設定してください。
会議開催日と公表日を混同しません。公表日時が不明な場合は不明とし、取得日時を記録。
`outputs/latest.json`と`outputs/actors.html`に結果を出力。

外部APIの実通信、Docker、GitHub Actions、24時間耐久運転は未検証。収集失敗時はdegraded、予測データ不足時はpending_data。

## 出来事と原文の管理

- RSS/APIから得た本文・概要を版ごとに保存。
- 引用の文字位置と内容が保存原文に一致することを検証。
- キーワードを含む文をpolicy_mention候補として自動抽出。
- 否定・仮定・引用された文も「候補」のまま。成立した法案と自動認定しません。
- 関係者の特定、文意、制度の成立・施行は別途確認が必要。
- 確認・否定・不明のレビュー理由と根拠を履歴として保存。

## 利害と行動の管理

利害は`benefit`（受益）、`burden`（負担）、`influence`（働きかけ）、`anticipation`（事前予測・準備）、`action`（行動）、`impact`（波及）を別軸で登録。
各記録には根拠、証拠の状態、競合する説明が必須。
受益だけから関与や意図を自動的に生成する機能はありません。

受益・負担リンクから、投資、参入、増産、費用削減、撤退、価格変更等の候補を定型ルールで生成。
「制度が実施され、利害仮説が正しく、資源・能力があるなら」という条件を表示します。
これは主体の意図を確認したものではなく、LLMの推論でもありません。
行動ラベルごとの確定履歴が10件未満なら確率はpending_data。
10件以上ならLaplace平滑化したベースレートを参考値として表示。文脈の違いは未補正、未校正です。

## 分析を登録する

出来事候補を抽出:

```bash
python -m wpe.actors --db data/runtime.sqlite extract
python -m wpe.actors --db data/runtime.sqlite report --output outputs/actors.html
```

レポートのイベントIDと証拠IDを参照して、`examples/analysis-template.json`をコピーし、対象に合わせて編集します。
人物・企業の同定は明示的に行い、同名を自動統合しません。

```bash
python -m wpe.actors --db data/runtime.sqlite import my-analysis.json
```

テンプレートの`EVENT_ID`、`EVIDENCE_ID`、企業名、日時、質問、判定ルール、推定値は置き換えが必要。
テンプレート中の確率はサンプル値で、現実の予測ではありません。
情報基準日以前に取得した証拠だけが予測へ登録できます。内容や因果関係の真実性まで引用一致で保証されるわけではありません。
予測の`created_at`は情報基準時刻、`recorded_at`は実際の登録時刻。後から登録した過去日の予測を前向き実績として数えないでください。
同じIDの予測は変更不可。更新は新しいIDとして登録し、評価時に同じ問題の重複を避けます。

イベントを確認する:

```bash
python -m wpe.actors --db data/runtime.sqlite review EVENT_ID --status uncertain --evidence EVIDENCE_ID --rationale "施行状況を確認できていない"
```

期限後に結果を登録:

```bash
python -m wpe.actors --db data/runtime.sqlite resolve FORECAST_ID --occurred 1 --evidence OUTCOME_EVIDENCE_ID --rationale "期限内の公式発表を確認"
```

負の結果は、記録がないというだけで登録しないでください。判定元の網羅性・検索範囲を確認できなければ未解決とします。
行動予測の確定は手動登録。v0.2の数値閾値予測の自動採点とは別です。

## 現在の到達点

自動収集→原文保存→言及候補抽出→利害分析の明示的登録→行動候補生成→期限付き予測登録→手動判定と採点→HTML表示まで実装。
LLMによる自動利害分析、企業情報の自動同定、校正済み行動予測、因果モデル、多国間統合は未実装。
制度変更から企業の行動を高精度に予測できることも、まだ実証していません。
仕様と今後の工程はdocs/V03-DESIGN.md。

## 運転・GitHub

v0.2のDockerfile、compose.yaml、自動テスト設定を引き継ぎました。

```bash
docker compose up -d --build
```

Docker実行は未検証。data/outputsはGitへ入れず、別にバックアップします。
APIの本文や企業資料を公開する場合は出典ごとの利用条件を確認し、動作例は合成・架空データを使います。
前版の説明はREADME-v0.1.md、README-v0.2.md。
