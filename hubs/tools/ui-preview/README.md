# UI 미리보기(ui-preview)

부팅·로그인·네트워크 없이 게임 UI 화면 하나를 Unity 배치모드로 띄워 PNG 로 남긴다. 게임 저장소에는 아무것도 커밋하지 않는다.

## 실행

```bash
PROJ=<게임 작업 공간 경로> bash hubs/tools/ui-preview/ui-preview.sh <화면|all>[,<화면>…] [가로x세로 …] [--stage <오토마 스테이지 Id>]
# 예
PROJ=~/orca/workspaces/LoveRevolutionMaker/<작업 공간> bash hubs/tools/ui-preview/ui-preview.sh all 1080x1920 1080x2340
```

- 해상도를 생략하면 1080x1920 이다.
- 배치모드(`-batchmode`)로만 실행한다. `-nographics` 를 쓰면 그리기 장치가 없어 PNG 가 나오지 않는다.
- 에디터가 같은 프로젝트를 열고 있으면 `build/ui-preview-project` 복제본(APFS 클론)에서 실행한다.
- 실행 동안 `Editor/` 를 대상 프로젝트의 `Assets/Tests/Editor/UiPreview` 에 복사해 넣고, 끝나면 지운다. 그 경로가 이미 있으면 멈춘다.
- 사전 조건: 로컬 게임 데이터 `build/game-data-local` (없으면 `node Tools/game-data/local/refresh.mjs --baseline pin`).

## 출력

- `<PROJ>/TestResults/ui-preview/<화면>_<가로>x<세로>_t0|t1|t2.png` — 연출 시작·절반·끝
- `summary.json` — 화면별 샷 경로, 샷 없음 이유, 오류
- `unity.log`
- 종료 코드: 0 성공 / 1 캡처 실패 / 2 실행 불가

## 방식

- 미리보기 씬은 메모리에만 만든다(카메라 + ScreenSpaceCamera 캔버스). 게임 캔버스는 ScreenSpaceOverlay 라 배치모드에서 화면 캡처가 안 되므로 RenderTexture 카메라로 그려 ReadPixels 로 저장한다.
- 캔버스 배율은 게임 SafeCanvasScaler 기준 해상도로 min(가로비, 세로비).
- 배치 에디터에는 DOTween 자동 갱신이 없어 Manual 갱신으로 절반까지 진행하고, CompleteAll 로 끝낸다.

## 화면 추가

`Editor/Automa/AutomaPreviewScreens.cs` 처럼 등록표에 한 줄을 더한다.

```csharp
yield return new UiPreviewScreen("화면-이름", root =>
{
    var dialog = UiPreviewPrefabs.Spawn<내Dialog>(nameof(내Dialog), root, true); // true=전체 화면, false=부품 원래 크기
    dialog.InitModal(픽스처로_만든_Data, null);
    dialog.OnModalDisplay();
});
```

- 데이터는 게임 Presenter 로 만들고 값은 시트 고정본(로컬 게임 데이터)에서 읽는다. 기획 값을 코드에 적지 않는다.
- 프리팹이 아직 없으면 `new MissingPreviewScreen("이름", "이유")` 로 두면 샷 대신 이유가 남는다.
- 새 파일이면 `Create` 를 `UiPreviewEntry.Execute` 의 등록표에 이어 붙인다.

## 알려진 문제

- 모달은 InitModal 을 한 번만 부르고, 레이아웃 갱신 뒤 private Render 를 리플렉션으로 다시 부른다(`UiPreviewDialogs.Init`). Render 인자 타입의 값을 Data 에서 못 찾으면 다시 그리기는 건너뛴다.
- 초상: `-executeMethod` 는 메인 스레드를 막아 Addressables 비동기 로드가 끝나지 않는다. 그래서 같은 주소(`characterportrait/<캐릭터 Id>`)의 에셋을 에디터 Addressables 설정에서 찾아 바로 넣는다. 캐릭터 Id 가 비면 넣지 않는다.
- 시트 고정본(pin) 오토마 상대 표에 CharacterId 값이 없어 지금은 초상이 비어 찍힌다. 고정본 갱신은 데이터 담당 몫이다.
- 미션 카드는 연출이 없어 세 장이 같다.
- 예고(AutomaNoticeDialog)·결과(AutomaMissionOutcomeDialog)·새 목표 소개(AutomaGoalIntroDialog)·미션 배너 화면은 프리팹·코드가 dev 에 올라오면 추가한다.
