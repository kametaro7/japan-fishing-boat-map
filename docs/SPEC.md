# 全国釣り船マップ データ仕様（作業用）

取得日の基準: 2026-09-15（`work/` 以下のファイルはリポジトリに含めていません）

## ディレクトリ

| パス | 内容 |
|---|---|
| `tools/scrape_<src>.py` | 掲載サイトごとのクローラ（キャッシュ付き・再開可能） |
| `work/cache/<src>/` | 取得した生HTML（URLのsha1などをファイル名に）。再実行時は再取得しない |
| `work/sources/<src>.json` | 掲載サイト由来の正規化レコード（下の「掲載サイトレコード」の配列） |
| `work/logs/<src>.log` | クロールのログ（進捗 `done/total` を定期的に1行出す） |
| `work/registry/raw/<NN>_*` | 都道府県の遊漁船業者登録簿の原本（PDF/Excel/HTML） |
| `work/registry/<NN>.json` | 登録簿を正規化したもの（下の「登録簿レコード」の配列）。NN は都道府県コード2桁 |
| `work/registry/<NN>.md` | その県の調査メモ（出典URL・時点・件数・取得できなかった理由など） |
| `work/discovery/` | 追加ソース調査の結果 |
| `work/tmp/<担当>/` | 各担当の一時ファイル |

## クロールの作法（必須）

- 1ホストにつき**直列**、リクエスト間隔は **0.8秒以上**。429/503 が出たら指数バックオフ（最大5回）。
- robots.txt の Disallow には従う。ログイン・予約フォーム・API の非公開エンドポイントは叩かない。
- 取得した HTML は必ず `work/cache/<src>/` に保存し、2回目以降はキャッシュから読む（`--refresh` 指定時のみ再取得）。
- User-Agent は一般的なブラウザ UA。`curl -m 60` / requests の timeout=60。
- 途中で落ちても再実行で続きから進むこと。進捗を `work/logs/<src>.log` に出すこと。
- 出力 JSON は UTF-8、`ensure_ascii=False`、配列。10〜50件ごとに途中保存しても良い（最後に完全版で上書き）。

## 掲載サイトレコード（work/sources/<src>.json）

```json
{
  "src": "chowari",
  "src_id": "00274",
  "src_url": "https://www.chowari.jp/ship/00274/",
  "name": "内浜丸",
  "kana": "うちはままる",
  "pref": "岡山県",
  "city": "倉敷市",
  "address": "岡山県倉敷市児島元浜町",
  "port": "元浜港",
  "lat": 34.457909, "lon": 133.807772,
  "tel": "086-000-0000",
  "website": "https://example.com/",
  "sns": ["https://www.instagram.com/xxx/"],
  "types": ["乗合", "仕立"],
  "targets": ["マダイ", "アオリイカ"],
  "methods": ["タイラバ", "ティップラン"],
  "holidays": "毎週水曜",
  "facilities": ["トイレ", "キャビン"],
  "capacity": 13,
  "access": "児島ICから2km、駐車場無料",
  "description": "（100字以内の要約。原文の長文コピーはしない）",
  "plans": [
    {
      "name": "タイラバで狙う！マダイプラン",
      "kind": "乗合",
      "targets": ["マダイ"],
      "price": 10000,
      "price_text": "10,000円/人（税込）",
      "depart": "06:00",
      "return": "13:00",
      "meet": "出船30分前",
      "season": "",
      "days": "",
      "includes": "乗船料",
      "url": "https://www.chowari.jp/ship/00274/plan/00004/"
    }
  ],
  "schedule_text": "（出船スケジュール・定期便などの一般的な記述があれば短く）",
  "fetched": "2026-09-15"
}
```

- 無い項目は `null` か空配列/空文字。**推測で埋めない**。
- `website` は**その船宿の公式サイト**（掲載サイト自身のURLは入れない）。ブログ/ホームページビルダー等でも公式ならOK。SNSは `sns` へ。
- `price` は1人あたりの基本料金（円・整数、税込が分かれば税込）。仕立で1隻料金なら `price_text` に「1隻○円」と書き、`price` は null、または `kind:"仕立"` として1隻料金を入れ `price_text` で明示。
- `depart` / `return` は `HH:MM`。午前便/午後便など複数あれば plans を分ける。
- `lat/lon` はサイトに座標がある場合のみ。無ければ null（後段で住所ジオコーディングする）。
- `pref` は「北海道/東京都/大阪府/京都府/〇〇県」の正式名。
- `stale`（任意）: 掲載元で現在は一覧に出ていない（休止・掲載終了の可能性がある）レコードは `true`。build.py はほかの掲載元と名寄せできた場合だけ使い、単独では地図に出さない。

## 登録簿レコード（work/registry/<NN>.json）

```json
{
  "src": "registry",
  "pref": "福井県",
  "reg_no": "15-0000",
  "operator": "（事業者名）",
  "office_name": "〇〇丸",
  "postal": "913-0000",
  "address": "福井県坂井市三国町（町名・番地）",
  "tel": "0776-00-0000",
  "boats": ["〇〇丸"],
  "area": "",
  "registered": "平成15年6月10日",
  "valid_until": "令和10年6月10日",
  "src_url": "https://www.pref.fukui.lg.jp/doc/suisan/tourokugyousha_d/fil/yugyosen_itiran_20260427.pdf",
  "as_of": "令和8年4月27日"
}
```

- 表に無い列は null。`address` は都道府県名から始まる形に揃える（原本に県名が無ければ付ける）。
- 1事業者で営業所が複数行なら行ごとに1レコード。
- 船名一覧・漁場（区域）が載っていれば `boats` / `area` へ。

## 都道府県コード

01北海道 02青森県 03岩手県 04宮城県 05秋田県 06山形県 07福島県 08茨城県 09栃木県 10群馬県
11埼玉県 12千葉県 13東京都 14神奈川県 15新潟県 16富山県 17石川県 18福井県 19山梨県 20長野県
21岐阜県 22静岡県 23愛知県 24三重県 25滋賀県 26京都府 27大阪府 28兵庫県 29奈良県 30和歌山県
31鳥取県 32島根県 33岡山県 34広島県 35山口県 36徳島県 37香川県 38愛媛県 39高知県 40福岡県
41佐賀県 42長崎県 43熊本県 44大分県 45宮崎県 46鹿児島県 47沖縄県
