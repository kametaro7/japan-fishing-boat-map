#!/bin/sh
# 亀井商店サイト（www.kameishouten.com/fishingmap/）へ、このサイト一式を反映する。
#   sh tools/publish_to_kameishouten.sh
# 反映先は GitHub Pages のリポジトリ kametaro7/kameishouten-site の作業ツリー。
set -e
SRC=$(cd "$(dirname "$0")/.." && pwd)
DST=${KAMEISHOUTEN_DIR:-$HOME/Desktop/kamei-shoten}
[ -d "$DST/.git" ] || { echo "反映先が見つかりません: $DST"; exit 1; }
rsync -a --delete --exclude 'work/' --exclude '.git/' --exclude 'tools/' --exclude 'docs/' \
      --exclude 'README.md' --exclude '.gitignore' --exclude 'CNAME' --exclude '.DS_Store' \
      "$SRC/" "$DST/fishingmap/"
cd "$DST"
git pull --rebase -q            # 同じサイトを別の作業が更新していることがある
git add fishingmap
git diff --cached --quiet && { echo "変更はありません"; exit 0; }
git commit -q -m "釣り船マップのデータを更新" -m "$(cd "$SRC" && git log --oneline -1)"
git push -q
echo "反映しました → https://www.kameishouten.com/fishingmap/"
