using System;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;

// HudRenderer·ModalRenderer 공용 캡처. 게임 저장소에 커밋 금지(렌더 스크립트가 함께 복사·제거).
// 배치모드 에디터는 DOTween 을 자동 갱신하지 않으므로 ManualUpdate 로 직접 진행시킨다.
// 파일: <이름>_t0.png(트윈 시작 직후) · _t1.png(중간) · _t2.png(전부 완료) · <이름>.png(= t2, 기존 호출 호환).
// -renderMotion 0 이면 트윈을 건드리지 않고 한 장만 찍는다. -renderMotionSec 로 중간 시점 계산용 총 길이 지정(기본 0.45).
public static class RenderShots
{
    const string TweenType = "DG.Tweening.DOTween, DOTween";

    public static void Capture(Camera cam, RenderTexture rt, int w, int h, string outDir, string name)
    {
        var args = Environment.GetCommandLineArgs();
        string Arg(string k, string d) { var i = Array.IndexOf(args, k); return i >= 0 && i + 1 < args.Length ? args[i + 1] : d; }
        var motion = Arg("-renderMotion", "1") != "0";
        var sec = float.TryParse(Arg("-renderMotionSec", "0.45"), out var v) ? v : 0.45f;
        var dotween = motion ? FindDotween() : null;
        if (dotween == null)
        {
            if (motion) Debug.LogWarning("DOTween 타입을 찾지 못함 - 모션 단계 없이 한 장만 캡처");
            Save(cam, rt, w, h, $"{outDir}/{name}.png");
            return;
        }
        Call(dotween, "ManualUpdate", 0f, 0f);
        Save(cam, rt, w, h, $"{outDir}/{name}_t0.png");
        Call(dotween, "ManualUpdate", sec * 0.5f, sec * 0.5f);
        Save(cam, rt, w, h, $"{outDir}/{name}_t1.png");
        Call(dotween, "CompleteAll", false);
        Canvas.ForceUpdateCanvases();
        Save(cam, rt, w, h, $"{outDir}/{name}_t2.png");
        File.Copy($"{outDir}/{name}_t2.png", $"{outDir}/{name}.png", true);
    }

    static Type FindDotween()
    {
        var t = Type.GetType(TweenType);
        if (t != null) return t;
        return AppDomain.CurrentDomain.GetAssemblies()
            .Select(a => a.GetType("DG.Tweening.DOTween", false)).FirstOrDefault(x => x != null);
    }

    static void Call(Type t, string method, params object[] a)
    {
        var m = t.GetMethods(BindingFlags.Public | BindingFlags.Static)
            .FirstOrDefault(x => x.Name == method && x.GetParameters().Length == a.Length);
        if (m == null) { Debug.LogWarning("DOTween." + method + " 없음"); return; }
        m.Invoke(null, a);
    }

    static void Save(Camera cam, RenderTexture rt, int w, int h, string path)
    {
        cam.Render();
        RenderTexture.active = rt;
        var tex = new Texture2D(w, h, TextureFormat.RGBA32, false);
        tex.ReadPixels(new Rect(0, 0, w, h), 0, 0); tex.Apply();
        File.WriteAllBytes(path, tex.EncodeToPNG());
        RenderTexture.active = null;
    }
}
