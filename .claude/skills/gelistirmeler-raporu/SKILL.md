---
name: gelistirmeler-raporu
description: GitHub Projects (v2) panosundaki işlerden "Geliştirmeler" listesi raporu ve 6 slaytlık sprint sunumu (Sprint'te Odaklandığımız İşler kanbanı, Metriklerimiz - Özet, Metriklerimiz, Metriklerimiz (Takıma özel), Canlıya Çıkmaya Hazır Olan İşler, Hesaplama Varsayımları) üretir; iki/çok sprintlik veride business review için sprint karşılaştırma slaytı da ekler; girdi GitHub Projects TSV/CSV dışa aktarımı veya API olabilir — statü renkli bullet'lar, sayfa üstünde statü lejantı ve Original Estimate üzerinden planlanan vs. tamamlanan iş grafikleri (KPI'lar, ilerleme çubuğu, statü ve kişi bazında efor). Kullanıcı "geliştirmeler listesi", "geliştirmeler raporu/sunumu", "proje panosundan liste çıkar", "planlanan vs tamamlanan", "sprint sunumu", "metriklerimiz", "canlıya hazır işler", "borusanotomotiv project 34" gibi bir şey istediğinde veya bir github.com/orgs/<org>/projects/<n>/views/<v> linki verip liste/rapor istediğinde kullan.
---

# Geliştirmeler Raporu

Bir GitHub organizasyon projesinin (Projects v2) item'larından, sunum slaytındaki
"Geliştirmeler" görseline benzeyen tek dosyalık bir HTML raporu üretir:

1. **Başlık + statü lejantı** — sayfanın üstünde her statü rengi, adı ve iş adedi.
   `✓` işaretli statüler "tamamlandı" sayılır. Lejanttaki statüye fare ile gelince
   GitHub'daki statü açıklaması görünür.
2. **Planlanan vs. tamamlanan iş** — `Original Estimate` alanının toplamı üzerinden:
   Toplam planlanan / Tamamlanan / Kalan / Tamamlanma oranı kutuları ve ilerleme çubuğu.
3. **Grafikler** — statü bazında efor dağılımı (statü renkli parça-bütün şeridi + tablo)
   ve kişi bazında planlanan vs. tamamlanan (bullet bar). Üzerine gelince tooltip.
4. **İş listesi** — 3 kolonlu, her iş başında **statü renginde bullet**, sağda efor
   rozeti, başlık issue/PR linkine gider. Varsayılan sıralama statü sırasına göredir.

Bullet renkleri doğrudan GitHub'daki Status seçeneklerinin renkleridir
(GRAY, BLUE, GREEN, YELLOW, ORANGE, RED, PINK, PURPLE); statüsü olmayan işler açık gri
"Statüsüz" olarak gösterilir.

## Varsayılanlar

Kullanıcının verdiği link:
`https://github.com/orgs/borusanotomotiv/projects/34/views/11?visibleFields=[…,402706858,…]`

| Parametre | Varsayılan | Not |
|---|---|---|
| `--org` | `borusanotomotiv` | URL'deki `orgs/<org>` |
| `--project` | `34` | URL'deki `projects/<n>` |
| `--view` | `11` | Görünümün kayıtlı filtresi uygulanır (`0` = filtre yok) |
| `--estimate-field-id` | `402706858` | `visibleFields` içindeki sayısal alan = Original Estimate |
| `--estimate-field` | `Original Estimate` | id bulunamazsa isimle, o da yoksa adında "estimate/tahmin" geçen sayı alanı |
| `--status-field` | `Status` | |
| `--done-status` | Done, Tamamlandı, Completed, Closed, Kapandı, Canlıda, Released, Yayında | Tekrarlanabilir; verilirse listeyi tamamen değiştirir |

Başka bir proje linki verilirse org / project / view değerlerini URL'den al; URL'de
`visibleFields` içinde sayısal bir id varsa ve kullanıcı efor alanından söz ediyorsa
onu `--estimate-field-id` olarak geç.

## Çalıştırma

Gereken: Python 3.9+ (yalnızca standart kütüphane) ve proje okuma yetkisi olan bir
GitHub kimliği — ya `gh auth login -s read:project` (veya
`gh auth refresh -s read:project`) ya da `read:project` kapsamlı bir `GITHUB_TOKEN`.
Organizasyon SSO kullanıyorsa token'ın o org için yetkilendirilmiş olması gerekir.

```bash
S=.claude/skills/gelistirmeler-raporu/scripts/build_report.py

# Canlı veriden (view 11 filtresiyle)
python3 $S -o gelistirmeler.html --unit SP --dump-json proje.json

# Sadece belirli bir sprint / ek filtre
python3 $S --extra-filter 'iteration:"Sprint 42"' -o sprint42.html

# Görünüm filtresi yerine kendi filtren
python3 $S --filter 'status:Done,"In Progress" -label:bug' -o rapor.html

# Kapalı issue'ları statüden bağımsız tamamlandı say, özel "done" statüleri
python3 $S --closed-as-done --done-status "Done" --done-status "Canlıya Alındı"

# GitHub'dan indirilen tablo (Projects > görünüm menüsü > Export view data, TSV/CSV)
python3 $S --from-export "BOD Project - Retail Sprints.tsv" --clean-titles -o gelistirmeler.html

# Ağ olmadan, daha önce kaydedilmiş veriden
python3 $S --from-json proje.json -o gelistirmeler.html
```

Diğer seçenekler: `--title` (varsayılan "Geliştirmeler"), `--unit` (efor birimi etiketi:
`SP`, `sa`, `gün`…), `--sort status|title|none`, `--columns N`, `--include-archived`,
`--me <login>` (filtredeki `@me` için). Tümü için `--help`.

## Adımlar

1. Kullanıcının linkinden org / proje / görünüm numarasını çıkar; farklı değilse
   varsayılanları kullan. Efor biriminin ne olduğunu (SP / saat / gün) biliyorsan
   `--unit` ver; bilmiyorsan boş bırak.
2. Betiği `--dump-json` ile çalıştır. Kimlik hatası alırsan kullanıcıdan
   `gh auth refresh -s read:project` çalıştırmasını veya token tanımlamasını iste —
   token'ı kendin aramaya çalışma.
3. Betiğin konsol çıktısındaki iş adedi, planlanan/tamamlanan toplam ve uygulanan
   filtreyi kullanıcıya bildir. Rapor altındaki notlarda "tahmini boş" iş sayısı ve
   atlanan filtreler yazar; bunları da aktar.
4. Çıktıyı göster: HTML dosyasını kullanıcıya gönder veya Artifact olarak yayınla.
   Slayta koymak için PNG gerekiyorsa Playwright/Chromium ile ekran görüntüsü al
   (1400px genişlik, `fullPage: true`) ya da tarayıcıdan yazdır.
5. Statü sırası/rengi yanlış görünüyorsa bunlar GitHub'daki Status alanından gelir;
   betikte değil projede düzeltilmesi gerekir.

## Sprint sunumu (6 slayt; çok sprintli veride 10 slayt; 16:9, HTML + PNG)

Kullanıcı slayt / sunum / sprint sunumu / 16:9 / PNG isterse `build_slides.py` kullan
(veri kaynağı seçenekleri `build_report.py` ile aynı).

**Hariç tutma:** Statüsü **Backlog** olan ve **RetroAction** etiketli işler tüm slayt ve
metriklerden çıkarılır (`--exclude-status`, `--exclude-label`); betik hariç tutulan iş
numaralarını konsola yazar.

**Statü renkleri:** Todo kırmızı · In Progress sarı · Review **gri** · Test turuncu ·
UAT **mor** · Done mavi · Released yeşil (tüm slaytlarda aynı).

Çıktı her zaman şu 6 slayttır:

1. **Sprint'te Odaklandığımız İşler** — kanban düzeni: 5 kolon (Yapılacak: Backlog/Todo ·
   Geliştirmede: In Progress/Review/Blocked · Test: Test/UAT · Tamamlandı: Done ·
   Canlıda: Released). Kolon genişliği içerdiği metin miktarıyla orantılı (dolu kolon en az
   260 px), kalabalık kolon kendi içinde sütunlara bölünür; yazı boyutu en kalabalık kolon sığana kadar küçülür.
   Kolonlarda kart değil **madde** (bullet) vardır. Her kolonun içinde maddeler iş tipine
   göre **her zaman şu sırayla** alt başlıklar altında gruplanır: **Proje → User Story →
   Task → Bug** (boş grup gösterilmez; alt başlıkta grup adı ve adedi yazar). Maddelerin
   üzerinde ayrıca tür etiketi yoktur; tür alt başlıktan okunur. İşler **Xurrent Talep No**'ya göre
   gruplanır (aynı talebin BE/FE/Jarvis işleri tek madde); talep no'su olmayan işler, sade
   başlıkları aynıysa (yalnızca BE/FE son eki farklı) birleşir. Her maddede: statü renginde
   bullet (grubun **en geride** kalan işinin statüsü), iş birimi diliyle **tema adı**; kolonda birden fazla statü varsa
   statü adı, grup birden fazla GitHub işinden oluşuyorsa **"N talep"**. `Original Sprint`
   işin sprintinden farklıysa sarı **"↪ Sprint N"** etiketi ve sarı vurgu.
2. **Metriklerimiz - Özet** — sade, yalnız adet (Original Estimate yok). Metrik slaytlarında
   "GitHub" kelimesi geçmez; birim **iş adedi**dir. Panel sırası:
   - **Planlanan ve aradan gelen işler**: cümleyle — "İlk planlanan N işe sprint içinde M iş
     eklendi", büyük **%x aradan giren iş** (= `AddedMidSprint` etiketli ÷ etiketsiz ilk planlanan);
     altında aradan gelen işlerin tema adları.
   - **Tamamlanma durumu**: "Son durumda toplam N iş içinden D iş tamamlandı", büyük
     **%y tamamlanma oranı** (iş adedi; iş tipi kırılımıyla aynı birim).
   - **Statü dağılımı**: tek kolon, yalnız **Xurrent talebi adedi** (talep, işleri içinde en geride
     kalanın statüsünde); kolon başlığı yok, "Xurrent" yazmaz. En altta **Toplam**; 0 satırlar gizli.
     `--ignore-xurrent` takımlarında tek kolon iş adedi.
   - **İş tipi kırılımı**: yalnız iki satır — **Geliştirme taleplerimiz** (User Story + Bug) ve
     **Proje işleri** (planlanan/tamamlanan iş adedi); Task'lar bu kırılımda yok (notta adedi yazar).
     Altında proje işlerinin tema adları listelenir (talep adından projeyi özetleyen iş birimi dili —
     tema adları `temalar.json`'da Claude tarafından yazılır).
   - **Altyapısal işler** (eski adıyla teknik borç): `TB` etiketli ÷ toplam iş; altında tema adları.
   - Lead time **iki ayrı kutu**: **Geliştirme işleri** (User Story + Bug) ve **Proje işleri**;
     takvim günü, iş adedi. (Satır 2 dört kutu: iş tipi, altyapısal işler, iki lead time.)
     Task'lar iki kutuya da girmez. İki sprinti birleştiren slaytlarda (toplam özet ve sprint
     karşılaştırması) lead time **yalnız geliştirme işleri** için gösterilir.
**Çok sprintli veri (ör. iki sprint birlikte export edildiğinde) slayt sırası:**
1. Sprint'te Odaklandığımız İşler (son sprint) → 2. **Sprint'te Odaklandığımız İşler - Sprint N & M**
   (tüm sprintlerin işleri tek kanbanda, bugünkü statüleriyle) → 3. Metriklerimiz - Özet (son sprint) →
4. **Metriklerimiz - Özet · Sprint N & M** (iki sprintin **toplam** verisi; karşılaştırma değil; yalnız
   5 kutu: Planlanan ve aradan gelen işler, Tamamlanma durumu, Statü dağılımı, İş tipi kırılımı,
   Lead time — altyapısal işler yok) → 5. Sprint karşılaştırması (aşağıda) → Metriklerimiz →
   Takıma özel → Canlıya Çıkmaya Hazır (son sprint) → **Canlıya Çıkmaya Hazır - Sprint N & M**
   (iki sprintin toplamı: release bekleyenler + bu sprintlerde canlıya alınanlar) → Hesaplama Varsayımları.

2b. **Metriklerimiz - Sprint N & Sprint M** (business review) — yalnız export'ta **birden fazla
   sprint** varsa üretilir (Özet'ten hemen sonra). Bu durumda diğer slaytlar **son sprint**
   için üretilir (`Sprint` alanı = son sprint); bu slayt tüm sprintleri yan yana karşılaştırır.
   **Altyapısal işler (teknik borç) bu slaytta yoktur.** Paneller: tamamlanma oranı (sprint
   başına + puan farkı), planlanan ve aradan gelen işler, sonraki sprinte aktarılan işler (son
   sprint için "devam eden"), iş tipi kırılımı (Geliştirme taleplerimiz / Proje işleri + proje
   adları), tamamlanan talepler (Xurrent talebi / ek talep), lead time (sprint başına medyan,
   ortalama + gruplu histogram). Sprint ataması: iş, ilk sprintinden (Original Sprint, yoksa
   Sprint) **Closed tarihinin düştüğü** sprinte kadar her sprintin kapsamındadır ve bittiği
   sprintte tamamlanmış sayılır (bitmediyse şimdiki Sprint'e kadar). Aradan gelen iş, ilk sprint
   ile oluşturulma tarihinin sprintinden hangisi sonraysa orada sayılır. `--sprint-start` şarttır;
   tek sunum isteniyorsa `--single-sprint`.
3. **Metriklerimiz** — adet bazlı (iş tipi, aradan gelen ve altyapısal işlerde OE de var):
   - Planlanan vs. tamamlanan: **Toplam iş** (iş tipi kırılımıyla aynı birim), **distinct Xurrent talebi**
     (talep, tüm işleri bitince tamamlanmış sayılır) ve **ek talepler** (Xurrent no'suz işler) — üç oran.
   - Statü dağılımı: Özet'teki tabloyla aynı (tek kolon, Xurrent talebi adedi).
   - İş tipi kırılımı: Geliştirme taleplerimiz / Proje işleri — iş adedi | Original Estimate; altında proje listesi.
   - Sprint ortasında gelen iş: `AddedMidSprint` etiketli ÷ ilk planlanan (etiketsiz),
     hem iş adedi hem OE; altında o işlerin tema adları.
   - Altyapısal işler: `TB` etiketli ÷ toplam, hem iş adedi hem OE; altında tema adları.
   - Lead time histogramı (iş adedi bazında, talep bazında değil; Closed tarihi boş biten işler hariç tutulur ve numaraları notta listelenir): yalnızca **Done ve Released** statüsündeki
     işler (`--lead-status`; Closed tarihi olup başka statüde olanlar sayılmaz). Süre,
     sprint başlangıcından Closed tarihine kadar geçen gün. `Original Sprint` doluysa o
     sprintin, boşsa işin `Sprint` alanındaki sprintin başlangıcı esas alınır; önceki
     sprintler mevcut sprintten geriye her biri `--sprint-days` (14) gün sayılarak bulunur.
4. **Metriklerimiz (Takıma özel)** — Original Estimate bazında planlanan vs. tamamlanan
   (toplam / Xurrent talepleri / ek talepler; iş bazında), statü dağılımı (OE) ve kişi
   bazında planlanan vs. tamamlanan (iş adedi | OE).
5. **Canlıya Çıkmaya Hazır Olan İşler** — gruplama slayt 1 ile aynıdır (Xurrent Talep No).
   - Sol: **release bekleyen** = statü `Done` + `Ready For Release` / `WaitingRelease` /
     `Waiting For Release` etiketi.
   - Sağ: **Bu sprintte canlıya alınanlar** = kanbandaki "Canlıda" kolonunun (Released vb.)
     **tüm** işleri, etiketten bağımsız — slayt 1 ile birebir tutarlı.
   - Bir talebin tüm işleri kriterdeyse tema adıyla; yalnız bir kısmı kriterdeyse o işler
     **GitHub başlığıyla** ve "↳ talep adı" notuyla listelenir.
   - Her iki kolonda Proje → User Story → Task → Bug alt başlıkları; "N talep" yalnız
     birden fazlaysa. Canlıya alınan çoksa sağ kolon genişler ve iki sütuna bölünür.
6. **Hesaplama Varsayımları** — takımın metrikleri doğru okuması için otomatik üretilen
   özet: kapsam ve hariç tutulan iş numaraları, sayım birimleri (Xurrent talebi / ek talep /
   GitHub işi ve hangi panelin hangisini kullandığı), tamamlandı statüleri ve kanban kolon
   eşlemesi, iş tipi kuralı, etiket bazlı metrik formülleri, lead time tanımı (takvim günü,
   sprint başlangıcı, Closed'ı boş olanlar), canlıya çıkış kriterleri, OE boşlukları ve
   betiğin veri uyarıları. Yeni bir kural/istisna eklendiğinde bu slayta da yansıt.

### Akış

```bash
S=.claude/skills/gelistirmeler-raporu/scripts
D=sunum/<takım>-<yyyy-mm-dd>
# 1) Grupları ve varsayılan adları çıkar
python3 $S/build_slides.py --from-export veri.tsv --team "<Takım>" --dump-themes $D/temalar-taslak.json -o $D/slaytlar.html
# 2) temalar.json yaz: her grup anahtarı → iş biriminin anlayacağı tema adı
# 3) Son hali üret ve PNG'ye çevir
python3 $S/build_slides.py --from-export veri.tsv --team "<Takım>" --sprint-start "Sprint 4=2026-10-05" --themes $D/temalar.json -o $D/slaytlar.html
node $S/export_png.mjs $D/slaytlar.html $D/slayt      # -> slayt-1..5.png (1920x1080)
```

Önceki bir sprintin `temalar.json`'u varsa yeni taslaktaki başlıkları eskisininkilerle
eşleştirerek adları taşı, yalnızca yeni temaları yaz.

**Tema adları (2. adım) Claude'un işidir:** taslaktaki her grubun `titles` listesine
bakıp müşterinin / iş biriminin anlayacağı kısa bir ad yaz — BE/FE/servis/Jarvis gibi
teknik ekleri, talep numarasını, "Hk." gibi kısaltmaları at; ne yapıldığını sonuç
diliyle söyle (ör. "Yeni Üyelik | ... Hatırlatma Maili BE/FE" → "Evraklarını
tamamlamayan yeni üyelere otomatik hatırlatma e-postası"). Başlıkta olmayan bilgi
uydurma; emin değilsen başlığa sadık kal. Bug'larda sorunu anlat ("... yaşanan hata").
`temalar.json` biçimi `{"<grup anahtarı>": "<ad>"}`; talep no'su olmayan farklı
anahtarlara **aynı ad** verilirse tek madde olarak birleşir (bunu yalnız Xurrent'ı
kullanmayan takımlarda, ör. Back Office, yap; Xurrent kullanan takımlarda gruplama yalnız
Xurrent Talep No'ya göredir, no'suz işlere ayrı adlar ver) ve yanında "N talep" yazar — aynı
talebin otomatik birleşmeyen parçalarını (ör. "Toplu Sevk BE İhtiyaçları" + "Toplu Sevk FE
Bağlamalar", "(SAP)" + "(Boomi)") böyle birleştir; anahtarlar taslaktakiyle birebir
aynı olmalı. Dosyayı çıktı klasöründe sakla (sonraki sprintte aynı talepler için
yeniden kullanılabilir).

### Alanlar ve etiketler

| Seçenek | Varsayılan | Not |
|---|---|---|
| `--xurrent-field` | `Xurrent Talep No` | Sütun esastır (başlıktaki numaradan farklı olabilir). Sütun yoksa ya da hücre boşsa başlığın başındaki numara (`82634239 - …`, `+82634239 - …`) kullanılır; boş hücre için uyarı basılır — kullanıcıya bildir |
| `--ignore-xurrent` | kapalı | **Takım istisnası (Back Office):** Xurrent Talep No yok sayılır; planlanan vs. tamamlanan yalnız toplam iş adedi, statü dağılımı tek kolon (iş adedi), OE paneli yalnız Toplam. Gruplama sadece BE/FE gibi parçalar üzerinden yapılır |
| `--origin-field` | `Original Sprint` | Değer `3` ya da `Sprint 3` olabilir. İşin `Sprint`'inden farklıysa "aktarıldı" belirteci gösterilir (aynıysa gösterilmez); sütun yoksa uyarı verir |
| `--sprint-field` | `Sprint` | Alt başlıkta gösterilir; lead time başlangıcı |
| `--sprint-start` | — | **Zorunlu (lead time için):** `'Sprint 4=2026-10-05'`; tekrarlanabilir. Tanımlanmayan sprintler numarası ve `--sprint-days` (14) ile türetilir. Export'ta sprint tarihi yoktur — kullanıcıya sor veya bilinen bir tarihten çıkar ve varsayımı söyle |
| `--mid-label` | `AddedMidSprint` | Sprint ortasında gelen iş |
| `--project-label` | `Project`, `Proje` | DD Project Name boşsa bu etiketi taşıyan iş de **Proje işi** sayılır |
| `--parent-field` | `Parent issue` | Proje adı türetirken kullanılan üst iş sütunu |
| `--signals` | — | Retro sinyallerini JSON'a yazar (Claude içgörü yazarken kullanır) |
| `--insights` | — | Claude'un yazdığı içgörü JSON'u → Retro İçgörüleri slaytı |
| `--single-sprint` | kapalı | Çok sprintli veride karşılaştırma slaytını üretme |
| `--tb-label` | `TB`, `Teknik Borç`, `Tech Debt` | Altyapısal işler (slaytlarda "teknik borç" yerine bu ad kullanılır) |
| `--ready-label` / `--ready-status` | `Ready For Release`, `WaitingRelease`, `Waiting For Release` / `Done` | Canlıya hazır (release bekleyen) |
| `--team` | proje adı | Alt başlıktaki takım adı |

Etiket eşleşmesi büyük/küçük harf, boşluk, `-` ve `_` farkını yok sayar.

- **İş tipi kuralı** (GitHub issue type yoksa): başlıkta `[BUG]` → Bug; `DD Project Name`
  dolu → Proje işi; başlıkta operasyonel anahtar kelime (işlemler/işlemleri, toplantı,
  takvim, kurulum, incele, migration, dependabot, destek süreci, deployment, task,
  dönüşleri, modelbuilder, dwh — düzenli ifade, Türkçe küçük harfle aranır) → Task; kalanlar
  User Story. Grubun kategorisi, işlerinin çoğunluk tipidir. Export'a `Type` sütunu
  eklenirse o kullanılır.
- Konsol çıktısındaki özet sayıları (Xurrent / ek / sprint ortası / altyapısal işler /
  canlıya hazır) ve uyarıları (eksik sütunlar) kullanıcıya aktar.
- PNG için Node + `playwright` gerekir; yoksa HTML'i tarayıcıda açıp yazdır → PDF.

## Retro içgörüleri (her sunumda)

Sunumu ürettikten sonra takım retrosu için içgörü de üret:
1. `--signals <klasör>/retro-sinyalleri.json` ile metrik özetini al: tamamlanma (iş ve OE), açık
   işlerin statü bazında adedi/eforu, kişi bazında açık yük, aradan gelen, altyapısal, proje
   işleri ve statüleri, 2+ sprinttir açık işler, release bekleyen, lead time (geliştirme/proje),
   eksik Closed tarihleri, veri uyarıları ve (çok sprintli veride) sprint karşılaştırması.
2. Sinyallere ve slaytlara bakarak `<klasör>/retro-icgoruler.json` yaz:
   `{"subtitle": "...", "cards": [{"tone": "risk|good|action|data", "title": "...", "text": "...",
   "bullets": ["..."]}], "questions": ["..."]}` — 6–8 kart, 4–6 retro sorusu. Her kart **somut
   sayıya** dayansın (ör. "açık eforun %70'i Test'te"), mümkünse bir öneri içersin. En az bir
   "iyi giden" kartı olsun. Bakılacaklar: darboğaz statüsü (Test/Review/UAT yığılması),
   tamamlanma ve OE tamamlanma düşüşü, büyük/bölünmemiş proje işleri, aktarılan ve uzun süredir
   açık işler, UAT beklemesi, yükün tek kişide toplanması, lead time değişimi, aradan gelen iş,
   veri hijyeni. **Kişi adı/kullanıcı adı yazma**; yükü "tek geliştirici" gibi anonim anlat.
   Metin `text`/`bullets` içinde `<b>` kullanılabilir. Sayıları sinyallerden doğrula.
3. `--insights <klasör>/retro-icgoruler.json` ile yeniden üret → **Retro İçgörüleri** slaytı
   (Hesaplama Varsayımları'ndan önce): solda renkli kartlar (Dikkat / İyi giden / Öneri / Veri
   kalitesi), sağda "Retroda konuşalım" soru listesi. İçgörüleri kullanıcıya sohbette de özetle.

## Proje adları

Proje işleri (iş tipi kırılımı altındaki listeler) **proje adına göre** toplanır ve
"N iş · D tamamlandı" ile gösterilir. Ad şu sırayla bulunur:
1. **DD Project Name** doluysa o.
2. Boşsa başlığın ön eki (`One Click Auction - ...` → "One Click Auction", `Renk Değişimi - ...`).
3. Ön ek yoksa Parent issue başlığı (export'ta URL değil başlık varsa), o da yoksa talebin tema adı.
4. `temalar.json` içindeki `"_projects"` eşlemesi en son uygulanır: anahtar `#issue_no` ya da
   türetilen ad, değer iş birimi diliyle proje adı. `--dump-themes` taslağında `_projects`
   bölümü türetilen adları ve altındaki başlıkları listeler; **Claude talep ve parent
   adlarından feyz alarak** anlaşılır proje adları yazar, aynı projeye ait farklı ön ekleri
   aynı ada eşler (ör. "Renk Değişimi" ve "İhale ekranları renk değişimi" → tek proje).

## Dışa aktarılan TSV/CSV ile çalışma

Kullanıcı GitHub'dan indirdiği bir `.tsv`/`.csv` verirse `--from-export` kullan; API
erişimi gerekmez. Dikkat edilecekler:
- Dosyada renk bilgisi yoktur; statü renkleri isimden atanır (Backlog gri,
  **Todo kırmızı**, In Progress sarı, Review mor, Test turuncu, UAT pembe, Blocked kırmızı,
  **Done mavi**, Released yeşil; bilinmeyenler gri). Bilinen statüler iş akışı sırasına dizilir
  (Backlog → Todo → In Progress → Review → Test → UAT → Done → Released).
- Takım başına ayrı export gelirse her biri için ayrı klasöre (`sunum/<takım>-<tarih>/`) üret.
  Back Office Squad için her zaman `--ignore-xurrent` kullan.
  Export'ta başka board'lardan satır varsa (`Board` sütununa bak) takımın board'una
  filtrele, ör. `--filter 'board:"Back Office"'`, ve dışarıda kalanları kullanıcıya söyle.
- Yalnızca görünümde açık olan sütunlar dışa aktarılır. `Original Estimate` sütunu
  yoksa grafikler **iş adedi** üzerinden çizilir ve raporda not düşülür — kullanıcıya
  eforlu oran için alanı görünüme ekleyip yeniden dışa aktarmasını öner.
- `--clean-titles` baştaki talep numaralarını (`82237192 - `) siler, `[BUG]:` → `[BUG]`.

## Filtre desteği

Görünümün kayıtlı filtresi GraphQL'den okunur ve yaygın alt kümesi yerelde uygulanır:
`is:open|closed|issue|pr|draft|archived`, `alan:değer1,değer2` (alan adları küçük harf,
boşluk yerine `-`; ör. `original-estimate:>=5`, `status:"In Progress"`), `-alan:değer`
(hariç tut), `no:alan`, `has:alan`, `assignee:`, `label:`, `repo:`, sayısal
`>`, `<`, `>=`, `<=`, `a..b` ve serbest metin (başlıkta arar). `@current`, `@next`,
`@today` gibi göreli değerler desteklenmez; atlanır ve raporda uyarı olarak yazılır —
bu durumda `--filter` ile açık değer ver.

## Veri formatı (`--dump-json` / `--from-json`)

```json
{
  "project": {"title": "...", "url": "..."},
  "view": {"name": "...", "filter": "..."},
  "statusField": "Status",
  "statusOptions": [{"name": "Done", "color": "GREEN", "description": "..."}],
  "numberFields": [{"name": "Original Estimate", "databaseId": 402706858}],
  "items": [{"title": "...", "url": "...", "type": "ISSUE", "state": "OPEN",
             "archived": false, "assignees": ["login"], "labels": [],
             "fields": {"Status": "Done", "Original Estimate": 5}}]
}
```

Örnek veri: `examples/sample.json` (görseldeki başlıklar, rastgele statü/efor) ve
ondan üretilmiş `examples/sample.png`.
