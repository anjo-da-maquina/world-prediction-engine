# Changelog

## 0.4.0 — 2026-10-07

- 統合要求仕様書を追加。
- 原文付きの地域指標、危機シナリオ、司法対応の台帳を追加。
- 警戒・反証・期限切れ・情報不足を分けた判定、通知の重複抑止を追加。
- シナリオの手動結果判定と地域の分母付き換算・微分・積分を追加。
- 定期収集からriskレポートを更新。
- 架空デモ、入力仕様、実装対応表、GitHub手順、CIを整備。

## 0.3.0

- 原文引用、制度への言及候補、利害関係台帳、条件付き行動候補、行動予測と採点。

## 0.2.0

- 公開情報の定期収集、取得ログ、数値変化、累積超過、経路サンプル。

## 0.1.0

- 時点別データ保存、閾値予測、時間順の評価、CLI。

## Collection reliability update

- Preserve HTTP error codes in collection failures.
- Retry failed sources after at most 300 seconds; successful polling intervals are unchanged.
- Include next_poll_at in source health and waiting status.
- Ignore removed sources when calculating current health; retain historical polls.
- Add Windows Python 3.11 CI and failure/recovery regression tests.
- Existing databases require no schema migration.
