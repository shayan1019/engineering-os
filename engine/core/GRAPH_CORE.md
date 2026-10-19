# GRAPH_CORE — Task Graph & Knowledge Graph Kuralları

> **Bu dosya bir ADAPTER'dır.** Kaynak materyal: `.agents/skills/graph-engineering/` — dokunulmaz. Stage 7 cutover (2026-09-08) sonrası kanonik unified elaboration `.agents/skills/fable-5-1/graph/`'dir (native Graph Engineering subsystem); bu dosya, native skill erişimi zayıf/doğrulanmamış runtime'lar için hâlâ geçerli cross-runtime compatibility adapter'ı olarak kalır — retire edilmedi.
> GRAPH_CORE, graph-engineering'in iki yarısını runtime-agnostik şekilde uygulanabilir kurallara indirger.

## Zorunluluk
HER oturumda yüklenir. FABLE_CORE ile birlikte çalışır.

---

## BÖLÜM 1 — TASK GRAPH: ZORUNLU (her görev)

Her görev, küçük de olsa, adım grafına dökülür:

1. **Adımları listele** ve gerçek/fake dependency ayır: B, A bittikten SONRA çalışabiliyorsa B→A bağımlıdır.
2. **Paralel fan-out yalnızca gerçek bağımsızlıkta**: ortak dosya/state yoksa paralel, yoksa sıralı.
3. **One-writer**: her dosyanın tek yazarı; merge owner tektir (parallel yazıcı yasak).
4. **Verifier ayrımı**: yazar kendi işini "PASS" olarak tek başına ilan edemez — bağımsız doğrulama (test/evidence/ikinci göz) gerekir.
5. **Bounded loops**: döngülerin maksimum iterasyonu + stop rule tanımlanır (ör. 3 deneme sonra insan onayı).
6. **Human gates**: irreversible/paralı/credential/deploy/taşıma-silme öncesi onay.
7. **"Model PASS dedi ≠ bitti"**: PASS = koşullu evidence (test geçti, hash eşit, dosya yerinde).

**Küçük görevde** bu 7 madde mental olarak ~10 saniyede geçilir; **büyük görevde** yazılı task graph üretilir (bağımlılıklar + sıra + doğrulama).

---

## BÖLÜM 2 — KNOWLEDGE GRAPH: KOŞULLU (yalnızca tetikleyicilerde)

9 aşamalı KG pipeline (scope → ontoloji → extraction → fusion → depolama → query → GraphRAG → serving → bakım) **her görevde ÇALIŞMAZ**.

### Tetikleyiciler (AÇIK):
- Ontoloji / schema tasarımı, entity–relation extraction
- Knowledge graph, GraphRAG, graph memory, entity graph kurulumu
- Yapılandırılmış hafıza/memory graph mimarisi
- Kullanıcı açıkça "graph çıkar / ontoloji kur / KG yap" dediğinde

### Kapalı kalma listesi (KAPALI — sadece task graph yeterli):
- Bug düzeltme, typo, UI/test değişikliği, tek dosya edit
- Komut çalıştırma, veri biçimlendirme, deploy, config
- Araştırma/rapor yazımı (KG gerektirmiyorsa)

### Şüphede:
- 9 aşama yerine sor: "Bu iş gerçekten entity–relation grafı üretiyor mu?" Cevap hayır → KG kapalı.

---

## Akış Özeti
```
görev
 ├─ Task Graph (her zaman): adım + dep + sıra + writer + verifier + gate + loop bound
 └─ KG Pipeline (yalnız tetikleyici varsa): 9 aşama
```
