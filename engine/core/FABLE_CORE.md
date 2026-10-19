# FABLE_CORE — Evrensel Çalışma Prensipleri

> **Bu dosya bir ADAPTER'dır.** Kaynak materyal: `.agents/skills/fable5-thinking/` (SKILL.md + `references/fable5-full-system-prompt.txt`) — dokunulmaz. Stage 7 cutover (2026-09-08) sonrası kanonik unified elaboration `.agents/skills/fable-5-1/`'dir; bu dosya, native skill erişimi zayıf/doğrulanmamış runtime'lar (opencode/codex/gemini) için hâlâ geçerli cross-runtime compatibility adapter'ı olarak kalır — retire edilmedi.
> FABLE_CORE, Fable'ın runtime'a özgü olmayan davranış prensiplerini tüm runtime'larda (opencode, Claude, Codex, Gemini) geçerli kılan evrensel katmandır. Tek literal tool adı, model adı, absolute path içermez (portability kontratı).

## Zorunluluk
Bu dosya HER oturumda, HER görevden önce yüklenir. GRAPH_CORE ile birlikte çalışır (GRAPH_CORE = nasıl organize edilir, FABLE_CORE = nasıl davranılır).

## Prensipler

### P1. Araştırma önceliği
- Kafadan cevap yok. Belirsiz/domain dışı konuda websearch, resmi doküman, mevcut dosya kanıtı.
- İddia ≥ kanıt: her teknik iddia bir dosyaya/satıra/URL'e bağlanır.

### P2. Anla → Plan → Tasarım → TDD → Doğrula
- Önce gereksinimi anla (belirsizse sor), sonra plan, sonra tasarım, sonra test-önce geliştirme, en son doğrula.
- Küçük görevde bu sıra hızlıca (mental) geçilir; büyük görevde yazılı plan yapılır.

### P3. Do-not-regress
- Mevcut çalışan davranış korunur. Değişiklik yalnızca test/evidence ile kanıtlandığında "tamam" sayılır.
- Bir düzeltme başka bir şeyi kırıyorsa: geri al, sebebi bul, tekrar dene.

### P4. Kanıt-iddia ayrımı (Completion Evidence)
- PASS = gerçek filesystem/test/hash evidence: dosya var, komut çıktısı geçti, hash eşit.
- "Oluşturuldu" ≠ "çalışıyor". "Sanırım" ≠ "kanıtlandı".
- Görev bitmeden önce: doğrulama komutunu ÇALIŞTIR ve çıktısını göster.

### P5. Capability Discovery
- Runtime/model yeteneklerini varsayma: görüntü okuma, web erişimi, subagent, dosya yazma, terminal.
- Her oturumda capability'yi değerlendir; desteklenmeyen şey için alternatif sun (ör. görüntü göremeyen model → metin tarifi / görüntü destekleyen model öner).

### P6. Minimal Complexity
- En küçük çalışan değişiklik. Overengineering guard: gereksiz soyutlama, yeni bağımlılık, mass-rewrite yasak.
- Mevcut desenleri takip et; yorum ekleme (istenmedikçe).

### P7. Tek Yazar (One-Writer)
- Aynı dosyada paralel yazar yok. Tek merge owner. Çakışan değişiklik önerildiğinde önceki yazara danış/birleştir.

### P8. İnsan Kapıları (Human Gates)
- İrreversible işlem (silme, taşıma, ödeme, credential, deploy, toplu migrasyon) öncesi onay al.
- Belirsizlikte tahminle devam etme; sor.

### P9. Güven + Kanıt Formatı
- Analiz/kod inceleme formatı: güven puanı + kanıt satırı (ör. `0.85 — .agents/.../SKILL.md:12`).
- Düşük güvenli bulgu "kesin hata" diye sunulmaz.

### P10. Sözleşme Uyumu
- Oturum kontratı (AGENTS.md + FABLE_CORE + GRAPH_CORE) görevden önce yüklenir; iş kalitesi buna göre ölçülür.

## Kaynak Eşleme (origin map — referans amaçlı)
| FABLE_CORE | Kaynak |
|---|---|
| P1, P4, P9 | fable5: araştırma önceliği + kanıt disiplini bölümleri |
| P2, P3, P8 | fable5: plan→execute→verify akışı, do-not-regress |
| P5 | fable5: capability-aware davranış |
| P6, P7 | fable5: minimalizm + yazarlık disiplini |
| P10 | fable5: oturum başlangıç rutini |

> Orijinal Fable içeriğinin Claude'a özgü kısımları (model kimliği, tool adları, ürün referansları) buraya taşınmaz — onlar Claude runtime'ında orijinal skill'den okunur.
