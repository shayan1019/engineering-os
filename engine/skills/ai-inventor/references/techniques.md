# Techniques — Divergence Catalog

Pick 2–3 per round. Each entry: what it does, when to reach for it,
and the prompt line that operates it. If a dedicated problem-solving skill
is installed (e.g. collision-zone/inversion sub-skills), you may load it
for depth; this catalog is the fast path.

## 1. Contradiction Resolution (TRIZ-lite)

- **Does:** finds the two requirements fighting each other and dissolves
  the fight instead of trading one off.
- **Reach when:** every existing solution "ya hız ya doğruluk" diyor.
- **Prompt:** "Bu tasarımda A ile B hangi adımda çatışıyor? Çatışmayı
  koruyan varsayımı kaldırıp ikisini birden veren mekanizma ne?"

## 2. Collision Zone

- **Does:** imports a working mechanism from an unrelated domain.
- **Reach when:** the domain's own solutions are exhausted or all similar.
- **Prompt:** "<X> sektörü bu problemi nasıl zaten çözüyor? O mekanizmanın
  iskeletini bu probleme taşı."

## 3. Inversion

- **Does:** flips the goal ("nasıl önlenir?" → "nasıl garanti edilir?").
- **Reach when:** everyone attacks the problem from the same side.
- **Prompt:** "İstenen sonucun tam tersini garanti eden sistem tasarlasak,
  o sistemde hangi bileşen ters çevrilince istediğimiz şey ortaya çıkar?"

## 4. Scale Game

- **Does:** tests the idea at extremes to expose hidden levers.
- **Reach when:** current solutions are tuned for 'normal' scale.
- **Prompt:** "Bu problemi 1000× büyük ölçekte yaşasak ne bozulur?
  O bozulma yeni bir ürünün çekirdeği olabilir mi?"

## 5. Constraint Tightening

- **Does:** adds a brutal constraint on purpose to force new shapes.
- **Reach when:** concepts cluster into safe variations.
- **Prompt:** "'Elektrik yok / kullanıcı okuma-yazma bilmiyor / maliyet %90
  düşmeli' kısıtıyla hâlâ çalışan mekanizma ne?"

## 6. SCAMPER Sweep

- **Does:** systematic verb pass (substitute/combine/adapt/modify/
  put-to-other-use/eliminate/reverse) over the closest existing solution.
- **Reach when:** you need breadth fast and have a concrete baseline artifact.
- **Prompt:** "En yakın mevcut çözümü al; her fiil için tek satır değişim
  öner, sadece mekanizma değiştirenleri tut."

## Selection Heuristic

Frame says "çelişki var" → 1. Frame says "hepsi benziyor" → 2 + 3.
Frame says "ölçek/limit şikayeti" → 4 + 5. Baseline artifact elinde → 6.
