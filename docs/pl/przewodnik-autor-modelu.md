# Jak przygotować model dla platformy MLAPI — przewodnik dla autorów modeli

Ten przewodnik mówi, jak zrobić z modelu obraz, który platforma **sama znajdzie w GHCR** (GitHub Container Registry),
jak poprosić o moc obliczeniową (CPU, pamięć, dysk, GPU) i czego się spodziewać po drodze. Instrukcja dla administratora jest
w [`instalacja-admin.md`](instalacja-admin.md).

## 1. Droga modelu w skrócie

```
push do main ──► GitHub Actions buduje obraz ──► ghcr.io/<organizacja>/<repo>:latest
      ──► poller platformy znajduje nowy obraz (do 5 min) ──► czyta etykiety mlapi.*
      ──► administrator zatwierdza zasoby (pierwsza wersja i każde zwiększenie)
      ──► platforma uruchamia model ──► klienci wołają go przez Router z kluczem API
```

Co trzeba mieć, żeby poller w ogóle zauważył Twój obraz — to jest cała lista:

| Warunek | Dlaczego |
|---|---|
| Repozytorium (i pakiet) należy do **organizacji GitHub, którą odpytuje platforma** | poller czyta pakiety tylko tej jednej organizacji; obrazy z kont prywatnych i innych organizacji są niewidoczne |
| Obraz ma tag **`latest`** | poller sprawdza wyłącznie `latest`; inne tagi są ignorowane |
| Obraz ma etykietę **`mlapi.model="true"`** | bez niej pakiet jest uznany za „nie model” i pomijany po cichu |
| Obraz jest dla **`linux/amd64`** (albo zawiera taki wpis, jeśli to obraz wieloarchitekturowy) | tylko ten wariant jest czytany i uruchamiany |
| Etykiety `mlapi.*` są poprawne, a nazwa modelu spełnia regułę (sekcja 4) | w przeciwnym razie obraz zostaje odrzucony |
| Zmienił się **digest** obrazu | nowy `latest` o tym samym digeście nie jest zgłaszany drugi raz |

Pakiet może być **prywatny** — platforma ma token do odczytu. Zapytaj administratora, jak nazywa się organizacja.

> **Ważne:** o odrzuceniu obrazu (zła etykieta, zła nazwa, konflikt nazwy) **dowiaduje się administrator** (log Supervisora,
> webhook `deploy.failed`) — Ty nie dostaniesz żadnego komunikatu. Dlatego sprawdź etykiety lokalnie (sekcja 6), zanim
> wypchniesz obraz, a gdy model się nie pojawia w ciągu ~10 minut, napisz do administratora.

## 2. Start: repozytorium z szablonu

1. W organizacji kliknij **Use this template** na repozytorium `mlapi-model-template` — powstaje Twoje repozytorium modelu.
2. Edytuj:
   - **`ml_server.py`** — Twój model. Opisz wejście w `ScoreRequest` (klienci zobaczą to jako schemat modelu pod
     `GET /models/{nazwa}/schema`), załaduj model w `load_model()`, zamień dane wejściowe/wyjściowe w `preprocess` /
     `postprocess`. **Zawsze ustaw `request_model`** — bez niego wejście nie jest sprawdzane.
   - **`requirements.txt`** — Twoje biblioteki (jest już `fastmlapi`).
   - **`Dockerfile`** — nazwa i zasoby (sekcja 3). Dla GPU użyj `Dockerfile.gpu` (zmień jego nazwę na `Dockerfile`).
3. Wypchnij do `main`. Workflow `.github/workflows/build-and-push.yml` zbuduje obraz i opublikuje
   `ghcr.io/<organizacja>/<repo>:latest`. Jeśli workflow nie może zapisać pakietu, w ustawieniach repozytorium/organizacji
   w *Actions → Workflow permissions* włącz „Read and write permissions”.

## 3. Prośba o moc obliczeniową = etykiety w Dockerfile

Platforma nie ma formularza ani ticketu. **Zasoby, o które prosisz, to etykiety obrazu.** Szablon ustawia je przez `ARG`:

```dockerfile
ARG MODEL_NAME=my-model
ARG MLAPI_CPU=1          # rdzenie: 0.5, 2, 500m
ARG MLAPI_MEMORY=2Gi     # pamięć: 512Mi, 4Gi
ARG MLAPI_GPU=false      # true = jedna karta GPU
ARG MLAPI_DISK=5Gi       # trwały cache na pobrane wagi (/cache)
ARG MLAPI_MODE=sync      # sync = POST /predict;  queue = długie zadania, POST /jobs

LABEL mlapi.model="true" \
      mlapi.model.name="${MODEL_NAME}" \
      mlapi.cpu="${MLAPI_CPU}" mlapi.memory="${MLAPI_MEMORY}" \
      mlapi.gpu="${MLAPI_GPU}" mlapi.disk="${MLAPI_DISK}" \
      mlapi.mode="${MLAPI_MODE}"
```

Wartości domyślne, gdy etykiety brak: 1 CPU, 2Gi pamięci, bez GPU, 5Gi dysku, tryb `sync`.

### Jak to działa z zatwierdzaniem

- **Pierwsza wersja zawsze czeka na administratora** — nic się nie uruchomi bez jego zgody.
- Każda następna wersja, która prosi o **więcej** CPU, pamięci, dysku albo włącza GPU, znów czeka na zgodę; **do tego czasu
  poprzednia wersja dalej działa**. Prośba o tyle samo lub mniej wdraża się automatycznie.
- Administrator może zatwierdzić **mniej, niż prosisz** (np. 1 CPU zamiast 2). Model dostanie wtedy dokładnie tyle i musi
  się w tym zmieścić — przekroczenie pamięci kończy się zabiciem kontenera (OOM).
- Nowszy obraz zastępuje oczekującą prośbę poprzedniego. **Odrzuconego digestu nie zgłasza się ponownie** — jeśli
  administrator odrzucił (z uwagą, np. „zmniejsz pamięć”), popraw etykiety i wypchnij **nową** zawartość obrazu.
- Zmiana trybu `sync` ↔ `queue` istniejącego modelu wymaga, żeby administrator najpierw usunął model.
- Nazwa należy do jednego obrazu: jeśli ktoś inny już używa tej nazwy, Twój obraz zostanie odrzucony jako konflikt.

### Jak dobrać liczby (nie zgaduj)

Administrator zatwierdzi sensowną prośbę szybciej. Zmierz przed wypchnięciem:

```shell
docker build -t my-model .
# uruchom z dokładnie takimi limitami, o jakie prosisz — jeśli działa tu, zadziała na platformie
docker run --rm -p 8000:8000 --cpus 2 --memory 4g my-model
docker stats          # w drugim terminalu: szczytowe użycie pamięci podczas typowych i największych żądań
```

- **Pamięć:** weź największe zaobserwowane użycie + zapas ~20–30%. Za mało = OOM, za dużo = dłuższe oczekiwanie na zgodę.
- **CPU:** tyle rdzeni, ile model realnie wykorzystuje; więcej niż to nie przyspieszy.
- **Dysk:** rozmiar wag + zapas. To trwały cache (`/cache`, dostępny w kodzie jako `self.cache_dir`), nie miejsce na dane
  klientów. Na Dockerze limit dysku nie jest egzekwowany, na Kubernetesie tak.
- **GPU:** włącz tylko, jeśli model naprawdę tego wymaga. Karta jest **współdzielona** z innymi modelami i VRAM nie jest
  izolowany — ustaw w kontrolerze `gpu_memory_fraction` (np. `0.25`), żeby nie zagłodzić pozostałych. Gdy żadna karta nie jest
  wolna, model ma stan `waiting_for_gpu` (nie błąd) i ruszy, gdy się zwolni.

### Wiadomość do administratora (szablon)

Platforma sama powiadamia administratora tylko przez webhook, o ile go skonfigurował. Napisz też do niego wprost:

```
Model: <nazwa>   Repo: <organizacja>/<repo>   Tryb: sync / queue
Proszę o: CPU <x>, pamięć <y>, dysk <z>, GPU: tak/nie
Uzasadnienie: szczytowa pamięć <…> GiB przy żądaniu o rozmiarze <…> (docker stats), czas odpowiedzi <…> s
Spodziewany ruch: ok. <n> żądań/min, w szczycie <m>; kto z niego korzysta: <…>
Wagi: <skąd, ile GB>; pobierane przy starcie do /cache
Potrzebne zmienne środowiskowe/sekrety (np. HF_TOKEN): tak/nie
Termin: <…>
```

## 4. Nazwa modelu i tożsamość

- `MODEL_NAME` (etykieta `mlapi.model.name`): małe litery, cyfry i `-`, do 40 znaków, nie zaczyna się i nie kończy na `-`,
  **nie kończy się na `-api`, `-worker`, `-cache`** (te końcówki są zarezerwowane). To nazwa, której używają klienci.
- Workflow z szablonu ustawia ją jako **nazwę repozytorium zapisaną małymi literami**. Repozytorium z wielką literą,
  podkreśleniem (`_`) lub zbyt długą nazwą daje niepoprawną nazwę modelu i odrzucenie obrazu — wtedy ustaw `MODEL_NAME`
  ręcznie w `Dockerfile` i usuń linię `MODEL_NAME=…` z `build-args` w workflow.
- **`model_name` w `ml_server.py` musi być równy `MODEL_NAME`.** Platforma po starcie pyta model o `/info` i porównuje; różnica
  kończy wdrożenie błędem „identity check failed”.

## 5. Zasady, które musi spełnić kontener

- Nasłuchuje na porcie **8000** i udostępnia `/health`, `/predict`, `/info`, `/schema` (robi to `fastmlapi`).
- Działa jako **zwykły użytkownik** (nie root) i ma zapisywalny `/cache` (szablon to robi).
- **Usypianie:** model bez ruchu przez 15 minut jest zatrzymywany i budzony przy następnym żądaniu, więc `load_model()`
  uruchamia się często. Start ma być szybki; **duże wagi pobieraj do `self.cache_dir` przy starcie** (cache przetrwa restart),
  zamiast pakować je do obrazu. Wagi z Hugging Face: ustaw `HF_HOME` na `self.cache_dir / "hf"` i przypnij rewizję
  (`revision="<hash>"`). Platforma czeka na koniec `load_model()` (do `DEPLOY_TIMEOUT`, domyślnie 30 min) — potem wraca do
  poprzedniej wersji.
- **Sekrety nie trafiają do obrazu.** Token (np. `HF_TOKEN`) administrator ustawia w konfiguracji modelu jako zmienną
  środowiskową; zmienne z prefiksem `FASTMLAPI_`, `MLAPI_`, `NVIDIA_`, `CUDA_` są zarezerwowane. Poproś o to w wiadomości
  z sekcji 3.
- **Sieć:** modele nie widzą się nawzajem ani Redisa platformy. Dostęp do internetu (np. pobranie wag) zależy od instalacji —
  zapytaj administratora, jeśli pobieranie się nie udaje.
- **Błędy:** treść błędów 5xx jest przed klientem **ukrywana** (platforma zamienia ją na ogólny komunikat, a `fastmlapi`
  uruchomiony przez platformę nie zwraca tekstu wyjątków). Szczegóły loguj na stdout — administrator je zobaczy. Błędy
  wejścia zwracaj jako **4xx z jasnym komunikatem** (np. 422) — te przechodzą do klienta bez zmian.
- **Tryb `queue`** (długie zadania): ustaw `MLAPI_MODE=queue` i użyj kolejki `fastmlapi`. Klient wysyła `POST /jobs` i odpytuje
  `GET /jobs/{id}`; ten sam plik `ml_server.py` działa jako API i jako worker (patrz jego koniec). Administrator musi mieć
  ustawiony `QUEUE_ACL_SECRET`. Maksymalny czas zadania ustawia administrator (`max_job_seconds`).

## 6. Sprawdź lokalnie, zanim wypchniesz

```shell
docker build -t my-model .
docker run --rm -p 8000:8000 my-model
curl -s localhost:8000/health
curl -s localhost:8000/info                     # "name" musi być równe MODEL_NAME
curl -s localhost:8000/schema
curl -s -X POST localhost:8000/predict -H 'Content-Type: application/json' -d '{"values": [1, 2, 3]}'
```

Etykiety, które zobaczy poller:

```shell
docker inspect -f '{{json .Config.Labels}}' my-model | python3 -m json.tool
```

Lista kontrolna przed `git push`:

- [ ] `mlapi.model` = `true`, a `mlapi.model.name` spełnia regułę z sekcji 4 i równa się `model_name` w kodzie
- [ ] `mlapi.cpu`, `mlapi.memory`, `mlapi.disk` to liczby z pomiaru (np. `2`, `4Gi`, `10Gi`), `mlapi.gpu` to `true` albo `false`
- [ ] `mlapi.mode` to `sync` albo `queue`
- [ ] kontener startuje jako nie-root i odpowiada na `/health`, `/info`, `/schema`, `/predict`
- [ ] model mieści się w pamięci i CPU z prośby (test z `--cpus` / `--memory`)
- [ ] wagi pobierane do `self.cache_dir`, brak sekretów w obrazie
- [ ] `request_model` opisuje wejście i ma `request_example`

## 7. Limity chroniące Twój model (opcjonalne)

Możesz zadeklarować własne limity etykietami (liczby całkowite):

| Etykieta | Znaczenie |
|---|---|
| `mlapi.rate.rpm` | żądań na minutę **na jeden klucz API** |
| `mlapi.rate.total_rpm` | żądań na minutę **łącznie, wszystkie klucze** |
| `mlapi.rate.concurrency` | ile żądań naraz (liczone na proces Routera) |
| `mlapi.queue.max_pending` | ile zadań może czekać w kolejce, zanim model odpowie `429` (tryb `queue`) |

Reguły: brak etykiety = wartości domyślne platformy; platforma **obniża** wartość do swojego sufitu i nie przyjmuje „0 =
bez limitu” od autora modelu; administrator może je nadpisać (jego wartość wygrywa). Limity służą Ci do ochrony kosztownego
modelu przed nadmiernym użyciem, nie do obchodzenia ochrony platformy. Zakomentowana linia z przykładem jest w `Dockerfile`.
Modele zbudowane ze starszą wersją `fastmlapi` ignorują `mlapi.queue.max_pending` — zaktualizuj bibliotekę.

## 8. Co dalej po wypchnięciu

1. Workflow kończy się zielono → obraz jest w GHCR. (Zakładka *Packages* w organizacji pokazuje pakiet.)
2. W ciągu kilku minut (domyślnie 5) platforma go zauważa. Administrator dostaje prośbę o zatwierdzenie.
3. Po zatwierdzeniu model przechodzi `deploying → starting → ready`. Pierwsze uruchomienie może trwać długo (pobranie wag).
4. Administrator wystawia klientom klucze do Twojego modelu. Klient wywołuje Router:

   ```shell
   curl -X POST https://<router>/predict -H 'X-API-Key: mlapi_…' -H 'Content-Type: application/json' \
        -d '{"model": "<nazwa>", "values": [1, 2, 3]}'
   ```

   Ciało to dokładnie pola z `ScoreRequest` plus `"model"`. `GET /models` (z kluczem) pokazuje stan modelu, a
   `GET /models/<nazwa>/schema` — schemat i przykład.
5. Nowa wersja: po prostu wypchnij do `main`. Jeśli nie prosi o więcej zasobów, wdraża się sama, a jeśli nie wstanie na
   czas, platforma wraca do poprzedniej.

## 9. Gdy coś nie działa

| Objaw | Co sprawdzić |
|---|---|
| Model nie pojawia się po ~10 min | repozytorium w dobrej organizacji? tag `latest`? etykieta `mlapi.model="true"`? obraz `linux/amd64`? nazwa zgodna z regułą? Poproś administratora o log Supervisora (szukaj „rejected” / „Skipping package”) |
| Odrzucony z uwagą administratora | popraw etykiety (zwykle za dużo zasobów) i wypchnij nowy obraz — ten sam digest nie jest zgłaszany ponownie |
| Konflikt nazwy | ta nazwa należy już do innego obrazu — wybierz inną `MODEL_NAME` |
| `failed`: „identity check failed” | `model_name` w kodzie ≠ `MODEL_NAME` / `mlapi.model.name` |
| `failed`: „timed out” | `load_model()` trwa za długo (np. wolne pobieranie wag) — skróć start lub poproś o większy `DEPLOY_TIMEOUT` |
| Kontener ginie / restartuje się | najczęściej OOM — model przekracza zatwierdzoną pamięć; zmierz zużycie i poproś o więcej |
| `waiting_for_gpu` | wszystkie karty zajęte; poczekaj lub poproś administratora o uśpienie innego modelu |
| Klient dostaje `503` z `Retry-After` | model startuje lub śpi (żądanie go budzi) — to normalne po dłuższej przerwie |
| Klient dostaje `429` | przekroczony limit (klucza lub modelu); `Retry-After` mówi, ile czekać |
| W odpowiedzi błędu 5xx nie ma szczegółów | tak ma być; szczegóły są w logach kontenera, poproś o nie administratora |
