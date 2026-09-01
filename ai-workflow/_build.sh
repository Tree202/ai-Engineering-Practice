#!/bin/sh
# 用法: ./_build.sh <输出文件> <标题> <导航标题> <上一页> <下一页> <正文文件>
sed -e "s|__TITLE__|$2|" -e "s|__NAV__|$3|" -e "s|__PREV__|$4|" -e "s|__NEXT__|$5|" _shared_head.txt > "$1"
cat "$6" >> "$1"
cat _shared_tail.txt >> "$1"
rm -f "$6"
echo "  写入 $1 ($(wc -c < "$1" | tr -d ' ') bytes)"
