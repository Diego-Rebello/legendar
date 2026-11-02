#!/bin/zsh
# Gera Legendar.app na raiz do projeto. O app guarda o caminho do projeto: se a pasta mudar, rode de novo.
set -eu
root=${0:A:h:h}
app=$root/Legendar.app
# Monta fora e só troca no fim: um erro de compilação não pode destruir o app que funciona.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
new=$tmp/Legendar.app
mkdir -p "$new/Contents/MacOS"
swiftc -O -swift-version 5 -parse-as-library "$root/gui/Legendar.swift" -o "$new/Contents/MacOS/Legendar"
cp "$root/gui/Info.plist" "$new/Contents/Info.plist"
plutil -replace LegendarRoot -string "$root" "$new/Contents/Info.plist"
plutil -lint -s "$new/Contents/Info.plist"
codesign --force --sign - "$new"
rm -rf "$app"
mv "$new" "$app"
echo "Gerado $app"
