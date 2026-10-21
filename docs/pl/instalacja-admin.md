# Instalacja platformy MLAPI — instrukcja dla administratora

Ta instrukcja prowadzi od czystej maszyny do działającej platformy: Router (publiczne wejście dla klientów), Supervisor
(panel i API administratora), Redis i modele uruchamiane jako kontenery. Opisuje instalację na **jednej maszynie z Dockerem**
(sekcje 1–9) oraz skrótowo na **Kubernetes/k3s** (sekcja 10). Instrukcja dla autorów modeli jest w osobnym pliku:
[`przewodnik-autor-modelu.md`](przewodnik-autor-modelu.md).

## 0. Jak to jest zbudowane (trzy minuty)

```
klient ──► Router (publiczny, :8000) ──► kontener modelu          Supervisor (tylko admin, :8001, tylko localhost)
              │ czyta                          ▲                     │ wdraża / usypia / pilnuje
              ▼                                └─────────────────────┤
            Redis ◄── trasy, klucze, schematy ─────────────────────┘
```

- **Router** sprawdza klucz API klienta, limity, i przekazuje żądanie do modelu. Nie ma stanu — wszystko jest w Redisie.
- **Supervisor** znajduje obrazy modeli (w GHCR lub lokalnie), czeka na Twoją zgodę na zasoby, uruchamia model, usypia
  nieużywane modele i budzi je na żądanie. Zarządzasz nim z panelu `/admin` albo przez API (`X-Admin-Key`).
- **Model** to obraz Dockera zbudowany z biblioteką `fastmlapi`, z etykietami `mlapi.*` mówiącymi, ile zasobów potrzebuje.
- Nic nie działa bez Twojej akceptacji: **pierwsza wersja modelu i każde zwiększenie CPU/pamięci/dysku/GPU czeka na
  zatwierdzenie**. Poprzednia wersja działa do tego czasu.

## 1. Czego potrzebujesz

- Maszyna z Linuksem (lub macOS do prób) z **Docker Engine i wtyczką Docker Compose** (`docker compose version`).
- `git`, `openssl` (do generowania haseł).
- Dla modeli GPU: sterownik NVIDIA i **NVIDIA Container Toolkit** na hoście. (Instalację z GPU opisano tu na podstawie
  konfiguracji; nie była uruchamiana na prawdziwej karcie — sprawdź ją po pierwszym wdrożeniu.)
- Do automatycznego znajdowania modeli: konto/organizacja GitHub, w której leżą repozytoria modeli, oraz **token klasyczny
  (PAT) z uprawnieniem `read:packages`**. Jeśli organizacja wymusza SSO, autoryzuj token dla organizacji.
- Do udostępnienia klientom spoza maszyny: domena i **TLS** (sekcja 7).

> **Uwaga o bezpieczeństwie.** Supervisor montuje gniazdo Dockera (`/var/run/docker.sock`), co jest równoważne uprawnieniom
> roota na hoście. Uruchamiaj ten stos tylko na maszynie, której ufasz, i nigdy nie wystawiaj portu Supervisora do sieci.

## 2. Pobranie kodu

Repozytoria `ML-API` (Router) i `MLAPI-Supervisor` muszą leżeć **obok siebie** — Compose buduje Router z `../ML-API`.

```shell
mkdir mlapi && cd mlapi
git clone https://github.com/AI-MED-AGH/ML-API.git
git clone https://github.com/AI-MED-AGH/MLAPI-Supervisor.git
```

Zmiany bezpieczeństwa (limity, izolacja sieci, audyt) są na razie na gałęziach, dopóki nie zostaną zmergowane do `main`.
Jeśli jeszcze nie są zmergowane:

```shell
git -C ML-API checkout security/router-rate-limits
git -C MLAPI-Supervisor checkout security/limits-and-hardening
```

## 3. Konfiguracja (`.env`)

```shell
cd MLAPI-Supervisor
cp .env.example .env
```

Wygeneruj hasła i wpisz je do `.env`:

```shell
openssl rand -hex 24      # → REDIS_PASSWORD
openssl rand -hex 24      # → QUEUE_REDIS_PASSWORD
openssl rand -hex 32      # → SESSION_SECRET   (bez tego wszyscy są wylogowani po każdym restarcie)
openssl rand -hex 24      # → QUEUE_ACL_SECRET (tylko jeśli będą modele „queue” — długie zadania)
```

### Klucz administratora

Compose wymaga ustawionego `ADMIN_API_KEYS`, więc przy pierwszym generowaniu podaj tymczasową wartość w powłoce
(hasła z poprzedniego kroku muszą już być w `.env`):

```shell
ADMIN_API_KEYS=x docker compose run --rm --no-deps supervisor python -m app.admin_key
```

Pierwsze uruchomienie buduje obraz (kilka minut). Polecenie wypisze **klucz** (pokazany raz — zapisz go w menedżerze
haseł) i **hash**. Do `.env` wpisz hash:

```
ADMIN_API_KEYS=<hash>
```

Kilku administratorów, z nazwami widocznymi w dzienniku audytu i przy decyzjach o zatwierdzeniu:
`ADMIN_API_KEYS=anna:<hash1>,jan:<hash2>`. Nazwa: litery, cyfry, `_`, `.`, `-`.

### Automatyczne znajdowanie modeli w GHCR (zalecane)

```
GHCR_ORG=nazwa-organizacji-na-github
GHCR_TOKEN=ghp_...        # token klasyczny z read:packages
```

Ten sam token służy do **pobierania prywatnych obrazów** na hoście. Bez tych dwóch wartości poller jest wyłączony — modele
można wtedy rejestrować tylko ręcznie (sekcja 6).

### Pozostałe ustawienia, które warto znać

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `POLL_INTERVAL` | 300 | co ile sekund sprawdzać GHCR |
| `DEPLOY_TIMEOUT` | 1800 | ile sekund czeka na start nowej wersji; potem automatyczny powrót do poprzedniej |
| `KEY_MAX_LIFETIME_SECONDS` | 0 | `0` = klucze klientów mogą nie wygasać; inaczej każdy klucz musi mieć termin nie dalszy niż ta wartość |
| `ENABLE_DOCS` | false | `true` włącza `/docs` i `/openapi.json` (domyślnie wyłączone) |
| `LIMIT_CEILING_*`, `DEFAULT_MAX_PENDING` | 600 / 6000 / 64 / 10000, 1000 | sufity limitów, o jakie może prosić autor modelu (sekcja 8) |
| `DATABASE_URL` | puste (SQLite) | adres zewnętrznego Postgresa, jeśli nie chcesz SQLite |

Ustawienia Routera (limity domyślne itd.) są opisane w `ML-API/.env.example`; Compose ich nie przekazuje, więc zmiana
wymaga dopisania zmiennych do sekcji `router.environment` w `docker-compose.yml`.

## 4. Uruchomienie

```shell
docker compose up -d --build
docker compose ps
curl -s localhost:8001/health      # Supervisor
curl -s localhost:8000/health      # Router
```

Oba powinny odpowiedzieć „ok”. Panel administratora: **http://localhost:8001/admin** — zaloguj się kluczem administratora
(nie hashem). Po zalogowaniu klucz nie jest przechowywany w przeglądarce; sesja to podpisane ciasteczko (8 h).

Zdalnie dostaniesz się do panelu tunelem SSH, bez wystawiania portu:

```shell
ssh -L 8001:127.0.0.1:8001 uzytkownik@serwer     # potem http://localhost:8001/admin
```

Kontenery uruchamiają się same po restarcie maszyny (`restart: unless-stopped`).

## 5. Sprawdzenie, że izolacja sieci działa

Stos tworzy dwie sieci: `mlapi-control` (wewnętrzna — Redis z kluczami i trasami; **modele tam nie wchodzą**) oraz
`mlapi-models`. Każdy model dostaje dodatkowo własną sieć `mlapi-net-<nazwa>`, więc modele nie widzą się nawzajem.
Ta konfiguracja była testowana na atrapie Dockera, nie na żywym silniku — po wdrożeniu pierwszego modelu sprawdź:

```shell
docker network ls | grep mlapi                              # są: mlapi-control, mlapi-models, mlapi-net-<model>
docker inspect mlapi-router --format '{{json .NetworkSettings.Networks}}' | grep -o 'mlapi-[a-z0-9-]*'
curl -s -X POST localhost:8000/predict -H 'X-API-Key: <klucz>' -H 'Content-Type: application/json' \
     -d '{"model":"<model>","data":{}}'                     # Router sięga do modelu
```

Jeśli Router nie może dotrzeć do modelu (`502`), najpierw sprawdź, czy kontener Routera jest w sieci modelu
(`DOCKER_PEERS` musi zawierać `mlapi-router,mlapi-supervisor` — tak ustawia `docker-compose.yml`).

## 6. Pierwszy model: rejestracja, zatwierdzenie, klucz dla klienta

W panelu lub przez API. Przykład w API:

```shell
export SUP=http://localhost:8001 ADMIN="X-Admin-Key: <twój klucz>"

# a) rejestracja — albo poczekaj aż poller znajdzie obraz w GHCR (do POLL_INTERVAL sekund)
curl -X POST $SUP/v1/models -H "$ADMIN" -H 'Content-Type: application/json' \
     -d '{"image": "ghcr.io/<organizacja>/<repo>:latest", "source": "ghcr"}'
#    lokalny obraz na tej maszynie:  {"image": "moj-model:latest", "source": "local"}

# b) co model chce dostać?
curl -s $SUP/v1/approvals -H "$ADMIN"

# c) zatwierdzenie (opcjonalnie z mniejszymi wartościami niż żądane)
curl -X POST $SUP/v1/approvals/1/approve -H "$ADMIN"
curl -X POST $SUP/v1/approvals/1/approve -H "$ADMIN" -H 'Content-Type: application/json' -d '{"cpu":"1","memory":"2Gi"}'
#    odrzucenie z uwagą:  POST /v1/approvals/1/reject  {"note": "za dużo pamięci, zmniejsz do 4Gi"}

# d) stan wdrożenia
curl -s $SUP/v1/models/<model> -H "$ADMIN"

# e) klucz dla klienta — pokazany jeden raz; można ograniczyć do modeli (z gwiazdką) i terminu
curl -X POST $SUP/v1/keys -H "$ADMIN" -H 'Content-Type: application/json' \
     -d '{"name": "szpital-xyz", "allowed_models": ["ecg-*"], "limits": {"rpm": 60}}'
```

Klient wywołuje Router (`X-API-Key: mlapi_…`):

```shell
curl -X POST http://serwer:8000/predict -H 'X-API-Key: mlapi_…' -H 'Content-Type: application/json' \
     -d '{"model": "<model>", "data": {…}}'
```

Odwołanie klucza: `DELETE /v1/keys/{id}` — przestaje działać natychmiast. Model, do którego klucz nie ma dostępu, wygląda
dla klienta tak samo jak model nieistniejący (`404`).

### Stany modelu

`deploying` → `starting` → `ready`. Nieużywany model **zasypia** po 15 min (`idle_timeout_s`, `0` = nigdy); pierwsze żądanie
dostaje `503` z `Retry-After` i budzi go. `waiting_for_gpu` — brak wolnego GPU. Nowa wersja, która nie wstanie w
`DEPLOY_TIMEOUT`, jest zastępowana poprzednią automatycznie.

### Powiadomienia (webhooki)

`POST /v1/observers/` rejestruje adres, który dostanie zdarzenia, m.in. `approval.requested` (autor modelu czeka na Twoją
decyzję), `deploy.failed`, `model.waiting_for_gpu`. Warto podpiąć to do komunikatora, bo **platforma nie ma innego sposobu
powiadomienia Cię o nowej prośbie o zasoby**.

## 7. Udostępnienie klientom (TLS)

Router domyślnie słucha tylko na `127.0.0.1:8000`. Najprościej: zostaw tak i postaw przed nim reverse proxy z TLS,
np. Caddy (certyfikat Let's Encrypt sam się odnawia):

```
# /etc/caddy/Caddyfile
modele.twoja-domena.pl {
    reverse_proxy 127.0.0.1:8000
}
```

Jeśli proxy działa na innej maszynie, ustaw w `.env` `ROUTER_BIND=0.0.0.0` i ogranicz dostęp do portu zaporą. Nigdy nie
publikuj portu Supervisora (8001). Limity per klucz Router liczy sam; limitów per adres IP nie ma — jeśli ich potrzebujesz,
dodaj je w proxy.

**Wolni czytelnicy.** Klient, który zamawia duże odpowiedzi (do kilkudziesięciu MB) i czyta je bardzo wolno albo wcale, trzyma
je w pamięci Routera. Router liczy tę pamięć (`MAX_INFLIGHT_RESPONSE_BYTES`, domyślnie 128 MiB; po przekroczeniu inni dostają
`503 RouterBusy`, a nie awarię) i rozłącza klienta, który nie odebrał dużej odpowiedzi w `RESPONSE_SEND_TIMEOUT` sekund
(domyślnie 120). Mimo to przez ten czas może zajmować budżet. Najlepiej ograniczyć to proxy, które **buforuje całe
odpowiedzi** — nginx robi to domyślnie (`proxy_buffering on`), a Ingress z `55-ingress.yaml` to nginx. Caddy domyślnie
przekazuje odpowiedź na bieżąco, więc nie zdejmuje tego z Routera. Limitu pamięci przypadającego na jeden klucz nie ma. Jeśli
widzisz `503 RouterBusy` bez dużego ruchu, sprawdź w logu Routera (`request key=… ms=…`), który klucz zamawia duże odpowiedzi,
i odwołaj go lub ogranicz (`rpm`).

## 8. Limity — kto co ustawia

| Poziom | Kto | Jak |
|---|---|---|
| Klucz klienta | admin | `limits` przy `POST /v1/keys` lub `PATCH /v1/keys/{id}`: `rpm` (żądania/min), `poll_rpm` (odpytania zadań/min), `jobs_per_day`, `max_outstanding_jobs`. `0` = bez limitu, brak pola = wartość domyślna Routera (120 / 600 / 1000 / 100) |
| Model | autor (etykiety `mlapi.rate.*`) | obniżane do sufitów `LIMIT_CEILING_*`, nigdy „bez limitu” |
| Model | admin | `PATCH /v1/models/{nazwa}/config` z `{"limits": {"rpm_per_key": 30, "rpm_total": 300, "max_concurrency": 4, "max_pending": 500}}` — **wartość admina wygrywa** i może przekraczać sufity; `{"limits": {}}` wraca do tego, co zadeklarował obraz |

Zużycie klucza: `GET /v1/keys/{id}/usage?days=7` (żądania, błędy i odrzucenia 429 na model i dzień). Każde żądanie z poprawnym
kluczem trafia też do logu Routera (`key=… model=… status=… ms=…`; nigdy sekret).

Znane ograniczenia: współbieżność modelu jest liczona **na proces Routera**; limit nieukończonych zadań i kolejki może
zostać przekroczony o kilka sztuk przy gwałtownym szczycie; dzienny limit zadań to okno stałe (po północy UTC można zużyć
dwie dzienne porcje w krótkim czasie). Modele zbudowane ze starszym `fastmlapi` ignorują `FASTMLAPI_MAX_PENDING`
(limit nieukończonych zadań w Routerze nadal działa).

## 9. Codzienna obsługa

| Zadanie | Jak |
|---|---|
| Aktualizacja platformy | `git pull` w obu repozytoriach, potem `docker compose up -d --build`. Migracje bazy idą same przy starcie |
| Dziennik audytu | `docker logs mlapi-supervisor 2>&1 \| grep app.audit` — każda zmiana i każda nieudana próba logowania, z nazwą admina, bez klucza |
| Rotacja klucza admina | dopisz nowy hash do `ADMIN_API_KEYS` (po przecinku), restart Supervisora, później usuń stary — to kończy też jego sesje |
| Kopia zapasowa | wolumen `supervisor_data` (`/data/supervisor.db`); przy Postgresie `pg_dump`. Redis nie wymaga kopii (Supervisor odbudowuje trasy i klucze w ciągu minuty) |
| Zmienne środowiskowe modelu (np. `HF_TOKEN`) | `PATCH /v1/models/{nazwa}/config` z `{"env": {"HF_TOKEN": "…"}}`. Nazwy z prefiksem `FASTMLAPI_`, `MLAPI_`, `NVIDIA_`, `CUDA_` są zarezerwowane |
| Wycofanie wersji | `POST /v1/models/{nazwa}/rollback`; ponowne wdrożenie: `…/redeploy`; uśpienie/obudzenie: `…/sleep`, `…/wake` |
| Usunięcie modelu | `DELETE /v1/models/{nazwa}` (dodaj `?keep_cache=true`, żeby zachować pobrane wagi) |
| Zatrzymanie wszystkiego | `docker compose down` (modele to osobne kontenery `mlapi-m-…`; Supervisor je usunie przy usuwaniu modelu) |

### Gdy coś nie działa

| Objaw | Prawdopodobna przyczyna |
|---|---|
| Router: `503 AuthBackendUnavailable` | Redis nie działa albo hasło w `REDIS_URL` różni się między Routerem a Supervisorem |
| Router: `404` dla istniejącego modelu | klucz nie ma modelu w `allowed_models` (celowo `404`) albo model nie został zatwierdzony/wdrożony |
| Router: `503 ModelUnavailable` | model się uruchamia, śpi (żądanie go budzi) albo czeka na GPU — patrz nagłówek `X-Model-Status` |
| Router: `429 RateLimited` | klient przekroczył limit; `Retry-After` mówi, ile czekać. Podnieś limit klucza lub modelu |
| Router: `503 RouterBusy` | Router trzyma w pamięci zbyt wiele odpowiedzi modeli naraz (`MAX_INFLIGHT_RESPONSE_BYTES`, domyślnie 128 MiB), zwykle przez wolnych klientów — patrz sekcja 7 |
| Model `failed`: „identity check failed” | `/info` obrazu zwraca inną nazwę niż etykieta `mlapi.model.name` |
| Model `failed`: „timed out” | model nie zgłosił `model_loaded` na czas (np. wolne pobieranie wag) — zwiększ `DEPLOY_TIMEOUT` |
| Model „queue” `failed`: `QUEUE_ACL_SECRET` | ustaw `QUEUE_ACL_SECRET` i wdróż ponownie |
| Obraz nie pojawia się mimo wypchnięcia do GHCR | poller widzi tylko tag `latest`, tylko pakiety organizacji z `GHCR_ORG`, tylko `linux/amd64` i tylko obrazy z etykietą `mlapi.model=true`; sprawdź `docker logs mlapi-supervisor` (szukaj „rejected” / „Skipping package”) |

Przyczyny odrzucenia obrazu przez poller (zła etykieta, zła nazwa) widać **tylko w logach Supervisora i w webhooku
`deploy.failed`** — autor modelu ich nie zobaczy, więc przekaż mu je.

## 10. Kubernetes / k3s (skrót)

Pełne polecenia są w `docs/runbook.md`, sekcja 3. Kolejność:

```shell
cd MLAPI-Supervisor
kubectl apply -f deploy/k8s/00-namespaces.yaml -f deploy/k8s/10-rbac.yaml -f deploy/k8s/20-quota.yaml
kubectl apply -f deploy/k8s/25-network-policies.yaml          # Redis i Supervisor dostępne tylko dla swoich
# sekrety (hasła Redis, ADMIN_API_KEYS, GHCR_TOKEN, QUEUE_ACL_SECRET, ghcr-pull) — patrz runbook
kubectl apply -f deploy/k8s/30-redis.yaml -f deploy/k8s/35-queue-redis.yaml
kubectl apply -f deploy/k8s/40-supervisor.yaml -f deploy/k8s/50-router.yaml
kubectl apply -f deploy/k8s/55-ingress.yaml                   # najpierw edytuj, patrz niżej
kubectl apply -f deploy/k8s/60-nvidia-time-slicing.yaml       # tylko węzły z GPU (4 „plastry” jednej karty)
```

- `55-ingress.yaml` zawiera **zaślepki**: host `models.example.com` i sekret TLS `mlapi-router-tls` — zmień je. Używa adnotacji
  **ingress-nginx** (`ingressClassName: nginx`); k3s domyślnie ma Traefika, więc zainstaluj ingress-nginx albo dostosuj plik.
  Ingress ma wskazywać **tylko Routera**, nigdy Supervisora.
- `20-quota.yaml` dopasuj do rozmiaru węzła (to ostateczny bezpiecznik zasobów).
- Zasady sieciowe wymagają wtyczki sieciowej, która je egzekwuje (k3s ma wbudowany kontroler).
- Manifesty Kubernetesa (Ingress, NetworkPolicy, HPA) były sprawdzane testami struktury, nie na żywym klastrze.
- Na Kubernetesie GPU jest dzielone w czasie między do 4 modeli **bez izolacji VRAM** — autorzy modeli mają ograniczać
  własne użycie (`gpu_memory_fraction`).
