#nullable enable
using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.AddressableAssets;
using UnityEngine;
using UnityEngine.UI;

namespace Tests.Editor.UiPreview
{
    /// <summary>
    ///     모달을 에디터 모드에서 실게임처럼 보이게 준비. InitModal 은 한 번만 부르고(행 목록 중복 방지),
    ///     레이아웃 확정 뒤 private Render 를 다시 불러 연출 기준 위치를 실제 위치로 맞춤.
    /// </summary>
    internal static class UiPreviewDialogs
    {
        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
        private const string RenderMethod = "Render";
        private const string DataProperty = "Data";
        // 대화창마다 초상 Image 필드 이름이 다름(하루·최종 결산 rivalPortrait, 미션 결과 portraitImage, 목표 소개 portrait)
        private static readonly string[] PortraitFields = { "rivalPortrait", "portraitImage", "portrait" };

        // 게임 CharacterPortraitSource 의 주소 규칙과 같음
        private const string PortraitAddressPrefix = "characterportrait/";

        /// <param name="portraitKey">상대 초상 캐릭터 Id. 초상 칸이 없는 화면은 null.</param>
        public static void Init(Component dialog, Action init, string? portraitKey = null)
        {
            init();
            Canvas.ForceUpdateCanvases();
            LayoutRebuilder.ForceRebuildLayoutImmediate((RectTransform)dialog.transform);
            Rerender(dialog);

            if (portraitKey != null)
            {
                ApplyPortrait(dialog, portraitKey);
            }
        }

        // Render 는 같은 행 목록을 Sync 해 행을 새로 만들지 않음. 연출 Prepare 도 그 안에서 다시 불림
        private static object? Rerender(Component dialog)
        {
            var render = dialog.GetType().GetMethods(Instance)
                .FirstOrDefault(method => method.Name == RenderMethod && method.GetParameters().Length == 1);
            var data = dialog.GetType().GetProperty(DataProperty, Instance)?.GetValue(dialog);
            if (render == null || data == null)
            {
                return null;
            }

            var stateType = render.GetParameters()[0].ParameterType;
            var state = stateType.IsInstanceOfType(data)
                ? data
                : data.GetType().GetProperties(Instance).FirstOrDefault(p => stateType.IsAssignableFrom(p.PropertyType))?.GetValue(data);
            if (state == null)
            {
                return null;
            }

            render.Invoke(dialog, new[] { state });
            return state;
        }

        // -executeMethod 는 메인 스레드를 막아 Addressables 비동기 로드가 끝나지 않음. 같은 주소의 에셋을 에디터에서 바로 읽음
        private static void ApplyPortrait(Component dialog, string key)
        {
            var image = PortraitFields
                .Select(name => dialog.GetType().GetField(name, Instance)?.GetValue(dialog) as Image)
                .FirstOrDefault(found => found != null);
            if (image == null)
            {
                Debug.LogWarning($"[UiPreview] {dialog.GetType().Name} 에 초상 Image 필드 없음({string.Join(", ", PortraitFields)})");
                return;
            }

            if (string.IsNullOrEmpty(key))
            {
                Debug.LogWarning($"[UiPreview] {dialog.GetType().Name} 초상 키 비어 있음. 픽스처 상대의 캐릭터 Id 확인");
                return;
            }

            var sprite = LoadAddressable<Sprite>(PortraitAddressPrefix + key);
            if (sprite == null)
            {
                Debug.LogWarning($"[UiPreview] 초상 주소 없음: {PortraitAddressPrefix}{key}");
                return;
            }

            Debug.Log($"[UiPreview] {dialog.GetType().Name} 초상 적용: {key}");
            image.sprite = sprite;
            image.enabled = true;
        }

        private static T? LoadAddressable<T>(string address) where T : UnityEngine.Object
        {
            var settings = AddressableAssetSettingsDefaultObject.Settings;
            var entry = settings?.groups.Where(group => group != null)
                .SelectMany(group => group.entries)
                .FirstOrDefault(item => item.address == address);
            if (entry == null)
            {
                return null;
            }

            var path = AssetDatabase.GUIDToAssetPath(entry.guid);
            return AssetDatabase.LoadAllAssetsAtPath(path).OfType<T>().FirstOrDefault()
                ?? AssetDatabase.LoadAssetAtPath<T>(path);
        }
    }
}
