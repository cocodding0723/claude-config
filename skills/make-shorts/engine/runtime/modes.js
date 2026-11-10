// 모드별 화면 틀. 수치는 참고 채널을 1080x1920 으로 환산해 잰 값이다.
export const MODES = {
  // 게임 개발 해설: 검정 바탕, 위 제목 두 줄(흰색 + 라임 노랑), 가운데 1080x1080 카드, 아래 420px 비움.
  gamedev: {
    bg: '#000000',
    sceneBg: '#071216',
    card: { x: 0, y: 420, w: 1080, h: 1080, r: 0 },
    full: { x: 0, y: 0, w: 1080, h: 1920 },
    title: {
      lines: [
        { y: 176, size: 90, weight: 900, color: '#FFFFFF', maxW: 0.9 },
        { y: 300, size: 108, weight: 900, color: '#E4FF0A', maxW: 0.9 },
      ],
      // 장면이 화면 전체를 덮을 때 제목 뒤에 까는 판.
      plate: { x: 40, y: 140, w: 1000, h: 300, r: 38, bg: 'rgba(0,0,0,0.44)' },
    },
    // 선언형 장면이 자막 자리를 침범하지 않게 카드 아래쪽을 비운다.
    stackReserve: 230,
    caption: { y: 1400, maxW: 960, size: 64, weight: 800, lh: 1.28, color: '#FFFFFF', box: 'rgba(0,0,0,0.65)', pad: '18px 34px', r: 26 },
  },
  // 코딩애플식 잡담: 검정 바탕, 제목 띠 없음. 세로 화면을 다 쓰도록 내용 영역을 세로로 길게 두고, 그 아래에 문장 자막을 둔다.
  // 바탕이 검정이라 자막 상자 없이 흰 글자만 쓴다. 노란 라벨·빨간 라벨과 섞이지 않게 색은 흰색 하나다.
  codingapple: {
    bg: '#000000',
    sceneBg: '#000000',
    card: { x: 0, y: 110, w: 1080, h: 1330, r: 0 },
    full: { x: 0, y: 0, w: 1080, h: 1920 },
    title: null,
    caption: { y: 1600, maxW: 980, size: 66, weight: 800, lh: 1.3, color: '#FFFFFF', box: 'transparent', pad: '0 20px', r: 0 },
  },
};
