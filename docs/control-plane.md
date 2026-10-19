# MY PROCECT — Ana Merkez (Hub) — Control Plane

Bu workspace; multi-model (opencode, Claude, Codex, Gemini) skill-aware, project-aware, regression-safe bir AI engineering kontrol düzlemidir. Tüm yollar bu dosyanın bulunduğu dizine (workspace root) göre RELATIVE'dir — absolute path içermez (portability).

## 1. Amaç ve Kapsam
- `.agents/` = kontrol düzlemi (core + registry + router + tools); `.agents/skills/` = tek kanonik skill kaynağı.
- Proje kaynak kodları (desktop projeleri dahil) orchestration gerektirmedikçe DEĞİŞTİRİLMEZ.
- Orijinal `fable5-thinking` ve `graph-engineering` skill'leri kaynak materyaldir — byte-level korunur, içerikleri rewrite/budama YAPILMAZ. Bunların native birleşimi `.agents/skills/fable-5-1/` artık kanonik unified metodoloji skill'idir (Fable disiplini + native Graph Engineering + capability-aware runtime + policy/evidence); normal rota `fable5-thinking`+`graph-engineering`'i ayrı ayrı zorunlu yüklemez (bkz. §2).
- Bakım araçları: `.agents/tools/bootstrap.ps1` (adapter kurulum, idempotent), `.agents/tools/doctor.ps1` (salt-okunur sağlık kontrolü).

## 2. Evrensel Çekirdekler (her görevden ÖNCE yükle)
- `.agents/core/FABLE_CORE.md` — davranış prensipleri (araştırma önceliği, kanıt-iddia, minimalizm, tek yazar, insan kapıları). ZORUNLU.
- `.agents/core/GRAPH_CORE.md` — Task-Graph kuralları ZORUNLU (bağımlılık, one-writer, verifier ayrımı, bounded loop, stop rule, human gate); Knowledge-Graph 9-aşama pipeline KOŞULLU (tetikleyici listesi dosyada).
- Kanonik unified metodoloji skill'i `.agents/skills/fable-5-1/`'dir. Claude runtime'ı bunu `.claude/skills` adapter'ı üzerinden native skill olarak okur; diğer runtime'lar (opencode/codex/gemini) aynı `.agents/skills/<name>/SKILL.md` yolundan doğrudan erişebilir. Skill-discovery'si zayıf/doğrulanmamış runtime'lar için FABLE_CORE/GRAPH_CORE cross-runtime distilled adapter olarak kalır (retirement Stage 7'de yapılmadı — bkz. COMPATIBILITY.json). `fable5-full-system-prompt.txt` hiçbir runtime'a ham enjekte edilmez.

## 3. Proje Çözümleme (Project Resolution)
- Kaynak: `.agents/registry/PROJECT_PROFILES.json` (path'ler relative; alias'lar içerir).
- Sıra: 1) görev metninde alias/keyword eşleşmesi, 2) cwd/discovery, 3) eşleşme yoksa YENİ PROJE rotası (§11).
- Bilinen projeler: BigHero → `../6.0_final_merge` (canonical; `../Aİ TRADER` legacy alias), AURA (`./AURA`), indicator, UPS, vpniran, alpha, liquid-demo, MY CV, tools, test backup (panel QA).
- Oturum root'ta açıldıysa hedef projenin kendi AGENTS.md/CLAUDE.md'si varsa onu da yükle (vpniran gibi), ama kök kontratın yerine geçmesine izin verme.

## 4. Skill Routing ve Aktivasyon
- Envanter: `.agents/registry/SKILL_CATALOG.json` (diskten `validate_skills.py --generate` ile üretilir — elle düzenlenmez; workspace + global kaynakları tarar, ilk kaynak önceliklidir).
- Aktivasyon kuralı: katalogdan göreve uygun MINİMUM-YETERLİ skill seti seçilir; 200+ skill'in tamamı yüklenmez (context tasarrufu).
- Çakışan tetikleme kümeleri: `.agents/registry/ROUTING_MAP.json` tekilleştirir (primary/fallback); `route_task.py` bu haritayı uygulayıp `skills.primary` döndürür.
- Runtime router: her görev başında `python .agents/router/route_task.py --task "<görev>"` çalıştır; `skills.primary` ile çalış, `skills.suppressed`'ı yükleme.
- `.agents/skills/<name>/SKILL.md` formatı zorunlu (depth-1, frontmatter: name + description).
- Skill içindeki runtime literal'leri (ör. `Skill("...")`) intent olarak oku; kendi runtime'ının native mekanizmasını kullan — bir runtime'ın tool syntax'ı diğerine taşınmaz (`.agents/registry/COMPATIBILITY.json` çeviri kuralları).

## 5. Runtime Capability Discovery
- Runtime/model yetenekleri (görüntü okuma, web erişimi, subagent, dosya yazma, terminal) varsayılmaz; oturumda değerlendirilir.
- Desteklenmeyen yetenek için alternatif sun (ör. görüntü destekleyen model/araç öner, metin tarifi iste).
- Runtime durumu ve doğrulama derecesi: `.agents/registry/COMPATIBILITY.json` (`NOT RUNTIME-VERIFIED` etiketli runtime'lar için statik doğrulama yeterlidir, runtime iddiası kurulmadıkça).

## 6. Çalışma Sırası
0. Yönlendirme: görevi `route_task.py --task "<görev>"` ile çöz (proje + risk + `skills.primary`); belirsiz kümede ROUTING_MAP primary'si kazanır.
1. Anla (belirsizse sor — tahminle devam etme) → 2. Plan (küçük görevde hızlı, büyük görevde yazılı task graph) → 3. Tasarım → 4. TDD/test-önce → 5. Doğrula.
- Araştırma gerektiren konuda websearch/resmi doküman kullan; kafadan cevap verme.
- Kod yazarken önce ilgili dosyaları oku, mevcut desenleri takip et; istenmedikçe yorum ekleme.
- Yanıt dili Türkçe; kod/komut çıktıları İngilizce kalabilir.

## 7. Do-Not-Regress
- Mevcut çalışan davranış korunur: proje test disiplinleri (BigHero suite/QC-audit/backtest/forward-test, AURA 490+ test suite, indicator RE formatı), audit/evidence kayıtları, panel QA kuralları.
- Değişiklik yalnızca test/evidence ile kanıtlandığında "tamam" sayılır. Bir düzeltme başka şeyi kırıyorsa: geri al, kök nedeni bul, tekrar dene.
- Silme/taşıma öncesi rollback imkânı sağla (backup/quarantine), sonra işle.

## 8. Doğrulama ve Kanıt (Completion Evidence)
- PASS = gerçek filesystem/test/hash kanıtı: dosya var, komut çıktısı geçti, hash eşit. "Oluşturuldu" ≠ "çalışıyor".
- Görev bitmeden önce doğrulama komutunu ÇALIŞTIR ve çıktıyı göster.
- Farklı runtime'ların aynı skill'de farklı davranışı beklenir; iddia her zaman runtime'a özgü kanıtla desteklenir.

## 9. Delegasyon ve Tek Yazar
- Roller: `.agents/registry/AGENT_REGISTRY.json` (explorer, architect, implementer, reviewer, verifier + runtime eşlemesi).
- One-writer: aynı dosyada tek yazar; merge owner tektir. Verifier ≠ writer.
- Delegasyon yalnızca gerçek fayda sağlıyorsa (paralel bağımsız iş, büyük keşif); küçük görevde inline kal.

## 10. İnsan Kapıları
- İrreversible işlemler (silme, taşıma, deploy, credential/ödeme, toplu migrasyon) öncesi onay al.
- Bounded loops: denemeler sınırlı; tıkanınca insana dön, aynı yolu tekrarlama.

## 11. Yeni Proje Rotası
- Eşleşme yoksa: `.agents/router/new_project.py` (interaktif: amaç → overlap kontrolü → tür → path → skill/agent/test önerisi → `--write` ile profil kaydı → validate_workspace).
- Kayıtlı projeler `PROJECT_INVENTORY.md` (Resolution Index) ile senkron tutulur (`validate_workspace.py` kontrol eder).

## 12. Analiz Formatları (Güven Puanı + Kanıt)
- Kod/indikatör analizi: `güven puanı + kanıt satırı` (ör. `0.85 — dosya:satır`). Düşük güvenli bulgu "kesin hata" diye sunulmaz.
- İndikatör tersine mühendislik: REVERSE_ENGINEER.md formatı korunur.
- Skill denetim/raporları: kanıt tabloları (sayı, hash, dosya yolu, tarih).

## 13. Doğrulama Araçları ve Bakım
- `validate_skills.py` — skill kütüphanesi denetimi (frontmatter, depth-1, duplicate, broken link, openai.yaml şeması); `--generate` ile katalog üretimi.
- `validate_workspace.py` — kontrol düzlemi bütünlüğü (AGENTS.md bölümleri, registry JSON, codex TOML, absolute-path sızıntısı, inventory senkronu).
- `route_tests.py` — 6 routing senaryosu + relocation simülasyonu (her değişiklik sonrası çalıştır).
- `doctor.ps1` — salt-okunur sağlık kontrolü; `bootstrap.ps1` — runtime adapter üretimi (junction/mirror, idempotent, path değişince yeniden çalıştır).
- Her faz sonunda ilgili aracı çalıştır; FAIL → sonraki destructive adıma geçme.