#!/usr/bin/env bash
# Launcher icons, splash screens and the sign-in mark, all from one source:
# mobile/brand/logo-source.jpg (the ErnestOS logo: green-and-white "E" and
# wordmark on black). Needs ImageMagick. Run from the repo root:
#   bash mobile/scripts/make-brand.sh
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=brand/logo-source.jpg
RES=android/app/src/main/res
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# The mark alone (the "E"), and the whole logo, each cut tight from the source.
convert "$SRC" -crop 1024x520+0+150 +repage -fuzz 12% -trim +repage "$TMP/mark.png"
convert "$SRC" -fuzz 12% -trim +repage "$TMP/full.png"
# The mark with its black ground made clear, for the adaptive foreground;
# the edge pixels left dark sit on the black background anyway.
convert "$TMP/mark.png" -fuzz 10% -transparent black "$TMP/mark-clear.png"

# The mark centred on a square canvas, `pct` of its height.
place(){ # size pct ground out
  local size=$1 pct=$2 ground=$3 out=$4 h=$(( $1 * $2 / 100 ))
  convert -size "${size}x${size}" "xc:$ground" \( "$TMP/mark.png" -resize "x${h}" \) \
    -gravity center -composite "$out"
}

for pair in mdpi:48 hdpi:72 xhdpi:96 xxhdpi:144 xxxhdpi:192; do
  d=${pair%%:*}; s=${pair##*:}; big=$(( s * 4 )); f=$(( s * 108 / 48 ))
  # Legacy square icon: black rounded tile.
  place $big 58 black "$TMP/sq.png"
  convert "$TMP/sq.png" \( -size ${big}x${big} xc:none -fill white \
      -draw "roundrectangle 0,0 $((big-1)),$((big-1)) $((big*22/100)),$((big*22/100))" \) \
    -compose CopyOpacity -composite -resize ${s}x${s} "$RES/mipmap-$d/ic_launcher.png"
  # Round icon.
  place $big 52 black "$TMP/rd.png"
  convert "$TMP/rd.png" \( -size ${big}x${big} xc:none -fill white \
      -draw "circle $((big/2)),$((big/2)) $((big/2)),0" \) \
    -compose CopyOpacity -composite -resize ${s}x${s} "$RES/mipmap-$d/ic_launcher_round.png"
  # Adaptive foreground: 108dp canvas, mark inside the 66dp safe zone.
  convert -size ${f}x${f} xc:none \( "$TMP/mark-clear.png" -resize "x$(( f * 50 / 100 ))" \) \
    -gravity center -composite "$RES/mipmap-$d/ic_launcher_foreground.png"
done

# Splash: the whole logo on black, a third of the short side wide.
for f in "$RES"/drawable*/splash.png; do
  read -r w h < <(identify -format "%w %h\n" "$f")
  short=$(( w < h ? w : h ))
  convert -size ${w}x${h} xc:black \( "$TMP/full.png" -resize "$(( short * 62 / 100 ))x" \) \
    -gravity center -composite "$f"
done

# Sign-in page and the web layer.
place 512 60 black "$TMP/tile.png"
convert "$TMP/tile.png" -resize 192x192 src/logo.png
convert "$TMP/full.png" -resize 640x src/logo-full.png
echo "brand assets written"
