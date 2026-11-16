using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;

// 모달 렌더 도구. 게임 저장소에 커밋 금지(PR 전 Assets/Editor/ModalRenderer.cs(+.meta) 제거).
// 게임 SafeCanvasScaler 와 같게 기준 1100x2100, 배율 = min(W/1100, H/2100)(Expand). 프리팹을 화면 가득(stretch)으로 단독 인스턴스.
public static class ModalRenderer
{
    public static void Run()
    {
        var args = Environment.GetCommandLineArgs();
        string Arg(string k, string d) { var i = Array.IndexOf(args, k); return i >= 0 && i + 1 < args.Length ? args[i + 1] : d; }
        var outDir = Arg("-renderOut", "TestResults/modalrender");
        var sizes = Arg("-renderSizes", "1080x1920,1080x2400,1440x3120,1536x2048,720x1280").Split(',');
        var prefabs = Arg("-renderPrefabs", "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        var tag = Arg("-renderTag", "shot");
        // 프리팹 루트(등장 트윈 시작값 0)와 지정 그룹만 켬. 나머지는 게임 기본 투명도 유지
        var show = Arg("-renderShowGroups", "").Split(',', StringSplitOptions.RemoveEmptyEntries);
        var bg = Arg("-renderBg", "");
        var hide = Arg("-renderHide", "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        Directory.CreateDirectory(outDir);
        foreach (var s in sizes)
        {
            var wh = s.Split('x'); int w = int.Parse(wh[0]), h = int.Parse(wh[1]);
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var camGo = new GameObject("cam"); var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor; cam.backgroundColor = new Color(0.45f, 0.5f, 0.55f, 1); cam.orthographic = true;
            var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32); cam.targetTexture = rt;
            var cgo = new GameObject("canvas", typeof(RectTransform));
            var canvas = cgo.AddComponent<Canvas>(); canvas.renderMode = RenderMode.ScreenSpaceCamera; canvas.worldCamera = cam; canvas.planeDistance = 10;
            var sc = cgo.AddComponent<CanvasScaler>(); sc.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            sc.referenceResolution = new Vector2(1100, 2100); sc.screenMatchMode = CanvasScaler.ScreenMatchMode.Expand;
            if (!string.IsNullOrEmpty(bg))
            {
                var spr = AssetDatabase.LoadAssetAtPath<Sprite>(bg);
                if (spr != null)
                {
                    var bgo = new GameObject("bg", typeof(RectTransform), typeof(Image));
                    bgo.transform.SetParent(cgo.transform, false);
                    var r = (RectTransform)bgo.transform; r.anchorMin = Vector2.zero; r.anchorMax = Vector2.one; r.offsetMin = r.offsetMax = Vector2.zero;
                    var im = bgo.GetComponent<Image>(); im.sprite = spr; im.preserveAspect = false;
                }
            }
            foreach (var p in prefabs)
            {
                var asset = AssetDatabase.LoadAssetAtPath<GameObject>(p.Trim());
                if (asset == null) { Debug.LogError("프리팹 없음 " + p); continue; }
                var inst = (GameObject)PrefabUtility.InstantiatePrefab(asset, cgo.transform);
                var rtf = inst.GetComponent<RectTransform>();
                if (rtf != null) { rtf.anchorMin = Vector2.zero; rtf.anchorMax = Vector2.one; rtf.offsetMin = Vector2.zero; rtf.offsetMax = Vector2.zero; }
            }
            foreach (var tr in cgo.GetComponentsInChildren<Transform>(true)) if (Array.IndexOf(hide, tr.name) >= 0) tr.gameObject.SetActive(false);
            foreach (var cg in cgo.GetComponentsInChildren<CanvasGroup>(true)) if (cg.transform.parent == cgo.transform || Array.IndexOf(show, cg.name) >= 0) cg.alpha = 1;
            foreach (var cv in cgo.GetComponentsInChildren<Canvas>(true))
                if (cv.gameObject != cgo) { cv.renderMode = RenderMode.ScreenSpaceCamera; cv.worldCamera = cam; cv.planeDistance = 10; }
            typeof(CanvasScaler).GetMethod("Handle", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance).Invoke(sc, null);
            foreach (var cv in cgo.GetComponentsInChildren<Canvas>(true)) if (cv.gameObject != cgo) cv.transform.localScale = Vector3.one;
            Canvas.ForceUpdateCanvases();
            RenderShots.Capture(cam, rt, w, h, outDir, $"{tag}_{w}x{h}");
        }
        EditorApplication.Exit(0);
    }
}
