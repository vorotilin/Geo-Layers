# План изучения предметной области — сервис гео-слоёв (`apc-layers`)

Материал подобран под задание из `brief.md` + `common-requirements.md`. Для каждой ссылки
указано «зачем она в этой задаче». Отмечай прочитанное галочками. Рекомендуемый порядок — в конце.

---

## 1. Фундамент: карта, проекция, тайл (начни отсюда)

- [+] [Web Mercator (EPSG:3857) — Wikipedia](https://en.wikipedia.org/wiki/Web_Mercator_projection) — почему OSM/MapLibre рисуют на сфере, и почему «квадрат 500 м» на карте не квадрат в метрах.
- [ ] [Slippy map tilenames — OSM Wiki](https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames) — **главная страница**: формулы z/x/y, схема XYZ, раздел «Resolution and Scale» (метров на пиксель = `156543.03 * cos(lat) / 2^zoom`). Без неё не сделать радиус KDE в метрах и «ячейка меньше пикселя».
- [ ] [EPSG:3857](https://epsg.io/3857) и [EPSG:3395](https://epsg.io/3395) — определения и параметры двух проекций, которые надо поддержать обе.
- [ ] [Mercator projection — Wikipedia](https://en.wikipedia.org/wiki/Mercator_projection) — база про искажения масштаба по широте.
- [ ] [Bing Maps Tile System / quadkey — Microsoft](https://learn.microsoft.com/en-us/bingmaps/articles/bing-maps-tile-system) — как устроен quadkey (формат «геометрия индексом»).
- [ ] [Geocentric Mercator paper — Zenodo](https://zenodo.org/records/6584844) — технически объясняет разницу «сфера (3857) vs эллипсоид (3395)», из-за которой тайлы Яндекса и OSM не совпадают.

## 2. Специфика Яндекс.Карт v3 (самое нетривиальное)

- [ ] [Yandex Maps JS API v3 — документация](https://yandex.com/maps-api/docs/js-api/) — это тот самый `ymaps3` из задания.
- [ ] [YMap — Yandex JS API](https://yandex.com/maps-api/docs/js-api/map/YMap.html) — у карты пропсы `projection` и `mode: raster | vector`.
- [ ] [Встроенные системы координат — Yandex (v1.1, для фона)](https://yandex.ru/dev/jsapi-v1-1/doc/ru/dg/concepts/cs-built-in) — как Яндекс описывает свои СК.

> Ключевой факт: **Web Mercator (3857) — меркатор на сфере; у Яндекса — меркатор на эллипсоиде WGS84 (3395)**. Одинаковые z/x/y указывают на разные точки → нужен отдельный рендер под каждую сетку.

## 3. Форматы данных (вход и выход)

- [ ] [GeoJSON — RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946) — координаты «долгота, широта» (CRS84), как в образце.
- [ ] [NDJSON — RFC 8142](https://datatracker.ietf.org/doc/html/rfc8142) — строковый формат для потоковой загрузки гигабайтных файлов.
- [ ] [GeoParquet](https://geoparquet.org) — колоночный формат для миллионов ячеек; Overture Maps раздаёт им миллиарды объектов.
- [ ] [flatgeobuf](https://github.com/flatgeobuf/flatgeobuf) — бинарный формат со случайным доступом: читать 3 ГБ без загрузки в память.
- [ ] [GeoPackage](https://www.geopackage.org) — векторный контейнер на SQLite (упомянут в задании).
- [ ] [Mapbox Vector Tile spec](https://github.com/mapbox/vector-tile-spec) — что такое MVT (желательная фича).
- [ ] [MBTiles spec](https://github.com/mapbox/mbtiles-spec) и [PMTiles](https://github.com/protomaps/PMTiles) + [PMTiles docs](https://docs.protomaps.com/pmtiles/) — контейнеры тайлов для экспорта.
- [ ] [GDAL](https://gdal.org) — читает XLSX/GPKG/GeoParquet/Shapefile, генерит тайлы; внешний бинар (разрешён по требованиям).

## 4. Пространственные индексы (H3, quadkey, S2…)

- [ ] [H3 — docs](https://h3geo.org/docs/), [h3 core (C)](https://github.com/uber/h3), [h3-py](https://github.com/uber/h3-py) — гексагональная сетка уровнями (бининг в H3 из задания).
- [ ] [H3: сравнение с S2/geohash/hexbin/admin — Uber](https://h3geo.org/docs/comparisons/s2) — когда H3, а когда произвольные полигоны.
- [ ] [S2 Geometry](https://s2geometry.io) — альтернатива (квадратная иерархическая сетка Google).
- [ ] [Geohash — Wikipedia](https://en.wikipedia.org/wiki/Geohash) — ещё один «геометрия строкой».
- [ ] [Geospatial Indexing: Geohash, Quadtree, R-tree, S2, H3](https://github.com/handbook-academy/engineering-handbook/blob/main/content/hld/part-2-building-blocks/14-geospatial-indexing.md) — один обзорный текст по всем индексам сразу.

## 5. Tile-серверы и отрисовка (проекты, которые стоит потыкать)

Прямые аналоги сервиса; смотри маршруты, кэш, стили, отдачу тайлов.

- [ ] [rio-tiler](https://github.com/cogeotiff/rio-tiler) + [docs](https://cogeotiff.github.io/rio-tiler/) — эталонный рендер растровых PNG по z/x/y; endpoint `/tiles/{z}/{x}/{y}` и колормапы.
- [ ] [titiler](https://github.com/developmentseed/titiler) + [docs](https://developmentseed.org/titiler/) — FastAPI-сервер на базе rio-tiler: стили, кэш, COG. Ближайший по духу.
- [ ] [Martin](https://github.com/maplibre/martin) — быстрый tile-сервер (PostGIS/MBTiles/PMTiles) на Rust; кэш и отдача MVT.
- [ ] [Tegola](https://github.com/go-spatial/tegola) и [t-rex](https://github.com/t-rex-tileserver/t-rex) — векторные tile-серверы (Go/Rust) для идей по производительности.
- [ ] [tippecanoe](https://github.com/felt/tippecanoe) — сборка MVT из миллионов точек; «точки → тайлы» и агрегация на мелких зумах.
- [ ] [COG (Cloud Optimized GeoTIFF)](https://www.cogeo.org) — растровые «картинки области» и range-requests.
- [ ] [MapServer](https://mapserver.org) и [GeoServer](https://geoserver.org) — классика: «что вообще делает картографический сервер».

## 6. Тепловые карты (KDE) — пункт 3 из задания

- [ ] [Kernel density estimation — Wikipedia](https://en.wikipedia.org/wiki/Kernel_density_estimation) — база: ядро, bandwidth, нормировка.
- [ ] [scikit-learn: Density estimation](https://scikit-learn.org/stable/modules/density.html) — как KDE считается на практике (bandwidth, ядра).
- [ ] [deck.gl HeatmapLayer](https://deck.gl/docs/api-reference/aggregation-layers/heatmap-layer) — **в точности твой кейс**: радиус и в пикселях, и в метрах (с коррекцией на проекцию), агрегация в «точки на км²».
- [ ] [QGIS: Heatmap (Kernel Density Estimation) / Interpolation](https://docs.qgis.org/latest/en/docs/user_manual/processing_algs/qgis/interpolation.html) — как GIS делает KDE с радиусом в метрах (потыкать в QGIS).
- [ ] [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) — визуальная подложка с радиусом в пикселях.
- [ ] [Mapbox heatmap style layer](https://docs.mapbox.com/style-spec/reference/layers/#heatmap) — рендер heatmap на стороне карты.

## 7. Биннинг, картограммы, классификация и палитры

- [ ] [deck.gl HexagonLayer](https://deck.gl/docs/api-reference/aggregation-layers/hexagon-layer) — агрегация точек в гексагоны (число/сумма/среднее).
- [ ] [mapclassify](https://pysal.org/mapclassify/) — разбиение на классы: equal interval, quantile, Fisher-Jenks (пункт 4 задания).
- [ ] [Jenks natural breaks — Wikipedia](https://en.wikipedia.org/wiki/Jenks_natural_breaks_optimization) — что такое Jenks.
- [ ] [ColorBrewer](https://colorbrewer2.org) — палитры sequential/diverging/qualitative для картограмм и легенд.
- [ ] [Colour scales — EU Data Visualisation Guide](https://data.europa.eu/apps/data-visualisation-guide/colour-for-numerical-data-colour-scales) — как выбирать шкалу и число классов.
- [ ] [Turf.js](https://turfjs.org) — `squareGrid`, `hexGrid`, `pointGrid`, `interpolate`, `isobands` в JS (логика в консоли).

## 8. Хранилище и большие данные (3 ГБ, миллионы ячеек)

- [ ] [PostGIS](https://postgis.net) — пространственная БД; geometry + GiST-индексы, версии/периоды ложатся естественно.
- [ ] [ST_AsMVT — PostGIS](https://postgis.net/docs/ST_AsMVT.html) — генерация векторных тайлов прямо из БД.
- [ ] [DuckDB spatial](https://duckdb.org/docs/stable/core_extensions/spatial/overview) — читать GeoParquet на 3 ГБ без загрузки в память, считать квантили/гистограммы.
- [ ] [GeoTrellis](https://github.com/locationtech/geotrellis) — Scala для растров/тайлов на big data; модель «layer → tile pyramid → render».

## 9. Карты в браузере (для веб-интерфейса и проверки)

- [ ] [MapLibre GL JS — docs](https://maplibre.org/maplibre-gl-js/docs/) — движок карты, на который задание просит веб-интерфейс.
- [ ] [MapLibre style spec: raster source](https://maplibre.org/maplibre-style-spec/sources/) и [layers](https://maplibre.org/maplibre-style-spec/layers/) — подключить тайл-URL как `raster` слой с непрозрачностью.
- [ ] [kepler.gl](https://github.com/keplergl/kepler.gl) и [deck.gl](https://deck.gl) — потыкать heatmap/hexbin/choropleth без сервера.

## 10. Похожие проекты целиком (загрузка → слои → тайлы + API + веб)

Ближе к задаче в целом, чем отдельные tile-серверы.

- [ ] [CARTO / CartoDB](https://github.com/CartoDB/cartodb) — ближайший полный аналог: импорт таблиц → стили/палитры → тайлы + SQL API + веб. Смотри «layer → version → tile url».
- [ ] [GeoNode](https://github.com/GeoNode/geonode) — self-hosted геопортал: загрузка, стили, тайлы (MapProxy), map composer, web UI.
- [ ] [geolens](https://github.com/geolens-io/geolens) — современный стек как в задаче: FastAPI + PostGIS + React + MapLibre, каталог + карты.
- [ ] [pg_tileserv](https://github.com/CrunchyData/pg_tileserv) — тонкий сервер PostGIS → MVT (парный к Martin, проще по коду).
- [ ] [TileServer GL](https://github.com/maptiler/tileserver-gl) — серверный рендер по GL-стилю: raster + vector тайлы из одного style.json (связка «стиль → тайлы + легенда»).
- [ ] [TileStache](https://github.com/TileStache/TileStache) — Python tile-сервер с **Grid provider** (растеризация сетки значений в тайлы), ближе всего к «сетка → цветной PNG», плюс heatmap.
- [ ] [datashader](https://github.com/holoviz/datashader) — растеризация миллиардов точек в density/heatmap за секунды.
- [ ] [xarray-spatial](https://github.com/makepath/xarray-spatial) — быстрые растровые операции, включая KDE с радиусом.

---

## Рекомендуемый порядок на 2 дня

**День 1 — понять «что это вообще такое».** Разделы 1 и 2 целиком (база всей задачи), затем в QGIS/deck.gl собери heatmap и картограмму из случайных точек — поймёшь, что должно получиться на выходе.

**День 2 — «как это делают».** Разделы 5, 8 и 10: подними `titiler`, `Martin` и `pg_tileserv` по их README, пощупай API и код отдачи тайлов/кэша. Затем разделы 6–7 (KDE/классификация) и 3–4 (форматы и H3).

Для каждой ссылки задавай вопрос: «как это закрывает конкретный пункт `brief.md`». Это и есть то понимание предметной области, которого сейчас не хватает.
