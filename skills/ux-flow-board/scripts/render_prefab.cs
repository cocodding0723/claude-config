// ux-flow-board: 프리팹을 PNG 로 렌더하는 임시 에디터 스크립트. Assets/Editor/_TmpRender/ 로 복사해서 쓰고, 끝나면 폴더째 지운다.
// 실행: UXBOARD_SPEC=<spec.json> Unity -batchmode -projectPath <proj> -executeMethod UxBoardRender.Run -logFile <log>
//       (-nographics 를 붙이면 카메라가 아무것도 안 그린다. 같은 프로젝트의 에디터가 열려 있으면 실행이 거부된다)
// spec.json: [{ "prefab": "Assets/.../X.prefab", "out": "/abs/x.png", "component": "DefaultDialog",
//               "texts":   { "title": "데이터 다운로드", "content": "..." },   // TMP_Text 필드 → 글자
//               "buttons": { "okButton": "받기", "cancelButton": "나중에" }, // Button 필드 → 자식 TMP 글자(켜짐)
//               "fills":   { "progressFill": 0.42 },                          // Image 필드 → fillAmount
//               "active":  [ "titleBox", "bottomButtonRow" ],                 // GameObject·컴포넌트 필드 → SetActive(true)
//               "inactive": [ "cancelButton" ] }]                               // 끌 것. 모달 스크립트를 떼고 그리므로 데이터가 끄던 버튼은 직접 꺼야 한다
using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;
using TMPro;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;

public static class UxBoardRender
{
    public static void Run()
    {
        var spec = JArray.Parse(File.ReadAllText(System.Environment.GetEnvironmentVariable("UXBOARD_SPEC")));
        foreach (JObject job in spec) RenderOne(job);
        EditorApplication.Exit(0);
    }

    private static GameObject GameObjectOf(object field) => field as GameObject ?? ((Component)field).gameObject;

    private static void RenderOne(JObject job)
    {
        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var cam = new GameObject("cam").AddComponent<Camera>();
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = new Color(0, 0, 0, 0);
        cam.orthographic = true;
        var canvasGo = new GameObject("canvas", typeof(Canvas), typeof(CanvasScaler));
        var canvas = canvasGo.GetComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceCamera;
        canvas.worldCamera = cam;
        canvas.planeDistance = 10;
        var scaler = canvasGo.GetComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1080, 1920);
        scaler.matchWidthOrHeight = 0f;

        var go = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>((string)job["prefab"]), canvasGo.transform);
        var comp = go.GetComponent((string)job["component"]);
        object Field(string name) => comp.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public)?.GetValue(comp);

        foreach (var kv in (JObject)job["texts"] ?? new JObject()) ((TMP_Text)Field(kv.Key)).text = (string)kv.Value;
        foreach (var kv in (JObject)job["buttons"] ?? new JObject())
        {
            var button = (Button)Field(kv.Key);
            button.gameObject.SetActive(true);
            button.GetComponentInChildren<TMP_Text>(true).text = (string)kv.Value;
        }
        foreach (var kv in (JObject)job["fills"] ?? new JObject()) ((Image)Field(kv.Key)).fillAmount = (float)kv.Value;
        foreach (var name in (JArray)job["active"] ?? new JArray()) GameObjectOf(Field((string)name)).SetActive(true);
        foreach (var name in (JArray)job["inactive"] ?? new JArray()) GameObjectOf(Field((string)name)).SetActive(false);
        Object.DestroyImmediate(comp); // 모달 수명주기(열기 연출·닫기)가 돌지 않게

        var rt = (RectTransform)go.transform;
        rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one; rt.offsetMin = Vector2.zero; rt.offsetMax = Vector2.zero;
        Canvas.ForceUpdateCanvases();
        foreach (var t in go.GetComponentsInChildren<TMP_Text>(true)) t.ForceMeshUpdate(true);
        LayoutRebuilder.ForceRebuildLayoutImmediate(rt);
        Canvas.ForceUpdateCanvases();

        var rtex = new RenderTexture(1080, 2400, 24, RenderTextureFormat.ARGB32);
        cam.targetTexture = rtex;
        cam.aspect = 1080f / 2400f;
        cam.Render();
        RenderTexture.active = rtex;
        var tex = new Texture2D(1080, 2400, TextureFormat.ARGB32, false);
        tex.ReadPixels(new Rect(0, 0, 1080, 2400), 0, 0);
        tex.Apply();
        File.WriteAllBytes((string)job["out"], tex.EncodeToPNG());
        RenderTexture.active = null;
        cam.targetTexture = null;
    }
}
