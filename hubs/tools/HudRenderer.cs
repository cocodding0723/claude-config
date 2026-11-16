using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;

// 렌더 도구. 게임 저장소에 커밋 금지(작업 공간 Assets/Editor 에 복사해 쓰고 PR 전 제거).
public static class HudRenderer
{
    const string Dir = "Assets/Game/SceneView/Scene/Design/UI_Prefab/Ingame/";

    public static void Run()
    {
        var args = Environment.GetCommandLineArgs();
        string Arg(string k, string d) { var i = Array.IndexOf(args, k); return i >= 0 && i + 1 < args.Length ? args[i + 1] : d; }
        var outDir = Arg("-renderOut", "TestResults/hudrender");
        var sizes = Arg("-renderSizes", "1080x1920").Split(',');
        var extra = Arg("-renderPrefabs", "IngameView/IngameView.prefab,Automa/AutomaMissionBanner.prefab").Split(',');
        var tag = Arg("-renderTag", "shot");
        Directory.CreateDirectory(outDir);
        foreach (var s in sizes)
        {
            var wh = s.Split('x'); int w = int.Parse(wh[0]), h = int.Parse(wh[1]);
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var camGo = new GameObject("cam"); var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor; cam.backgroundColor = new Color(0.2f, 0.2f, 0.25f, 1);
            cam.orthographic = true;
            var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32); cam.targetTexture = rt;
            var cgo = new GameObject("canvas", typeof(RectTransform));
            var canvas = cgo.AddComponent<Canvas>(); canvas.renderMode = RenderMode.ScreenSpaceCamera; canvas.worldCamera = cam; canvas.planeDistance = 10;
            var sc = cgo.AddComponent<CanvasScaler>(); sc.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            sc.referenceResolution = new Vector2(1080, 1920); sc.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight; sc.matchWidthOrHeight = 0.5f;
            foreach (var p in extra)
            {
                var asset = AssetDatabase.LoadAssetAtPath<GameObject>(p.Trim().StartsWith("Assets/") ? p.Trim() : Dir + p.Trim());
                if (asset == null) { Debug.LogError("없음 " + p); continue; }
                var inst = (GameObject)PrefabUtility.InstantiatePrefab(asset, cgo.transform);
                var rtf = inst.GetComponent<RectTransform>();
                if (rtf != null && p.Contains("IngameView.prefab") && !p.Contains("Top"))
                { rtf.anchorMin = Vector2.zero; rtf.anchorMax = Vector2.one; rtf.offsetMin = Vector2.zero; rtf.offsetMax = Vector2.zero; }
            }
            foreach (var cg in cgo.GetComponentsInChildren<CanvasGroup>(true)) { Debug.Log($"CG {cg.name} a={cg.alpha} act={cg.gameObject.activeSelf}"); cg.alpha = 1; }
            if (Arg("-renderActivateAll", "0") == "1") foreach (var tr in cgo.GetComponentsInChildren<Transform>(true)) tr.gameObject.SetActive(true);
            foreach (var tr in cgo.GetComponentsInChildren<Transform>(true)) if (tr.parent == cgo.transform || (tr.parent != null && tr.parent.parent == cgo.transform)) Debug.Log($"TR {tr.name} act={tr.gameObject.activeSelf} kids={tr.childCount}");
            foreach (var cv in cgo.GetComponentsInChildren<Canvas>(true)) { Debug.Log($"CV {cv.name} mode={cv.renderMode} root={cv.isRootCanvas}"); if (cv.gameObject != cgo && cv.isRootCanvas == false) { } }
            foreach (var cv in cgo.GetComponentsInChildren<Canvas>(true)) { if (cv.gameObject != cgo) { if (cv.renderMode != RenderMode.ScreenSpaceCamera) { cv.renderMode = RenderMode.ScreenSpaceCamera; } cv.worldCamera = cam; cv.planeDistance = 10; } }
            { var iv = cgo.transform.Find("IngameView"); if (iv != null) { var c = iv.GetComponent<Canvas>(); Debug.Log($"IVC scale={iv.localScale} ovr={c.overrideSorting} order={c.sortingOrder} layer={c.sortingLayerName} pd={c.planeDistance} z={iv.position.z}");
              foreach (var im in iv.GetComponentsInChildren<Image>(false)) { Debug.Log($"IMG {im.name} en={im.enabled} sp={(im.sprite!=null)} col={im.color} ca={im.canvasRenderer.GetAlpha()} cull={im.canvasRenderer.cull} sz={im.rectTransform.rect.size}"); } } }
            typeof(CanvasScaler).GetMethod("Handle", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance).Invoke(sc, null);
            foreach (var cv in cgo.GetComponentsInChildren<Canvas>(true)) { if (cv.gameObject != cgo && cv.renderMode != RenderMode.WorldSpace) cv.transform.localScale = Vector3.one; }
            Canvas.ForceUpdateCanvases();
            foreach (var r in cgo.GetComponentsInChildren<RectTransform>(true)) { if (r.parent != null && r.parent.parent != null && r.parent.parent.parent != null && r.parent.parent.parent.parent == null || r.parent == cgo.transform) Debug.Log($"RT {r.name} child={r.childCount} act={r.gameObject.activeInHierarchy} rect={r.rect} wpos={r.position}"); }
            RenderShots.Capture(cam, rt, w, h, outDir, $"{tag}_{w}x{h}");
        }
        EditorApplication.Exit(0);
    }
}
