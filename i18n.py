# -*- coding: utf-8 -*-
"""
Internationalization (i18n) Module for Discord Music Bot Cluster
Supports English (en), Traditional Chinese (zh-TW), Simplified Chinese (zh-CN),
Japanese (ja), Korean (ko), and Spanish (es).
"""

from typing import Optional, Dict, Any
import discord

SUPPORTED_LANGUAGES = {
    'en': 'English 🇺🇸',
    'zh-TW': '繁體中文 🇹🇼',
    'zh-CN': '简体中文 🇨🇳',
    'ja': '日本語 🇯🇵',
    'ko': '한국어 🇰🇷',
    'es': 'Español 🇪🇸'
}

DEFAULT_LANGUAGE = 'zh-TW'

# Per-guild custom language preference: guild_id -> lang_code
GUILD_LANGUAGES: Dict[int, str] = {}

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    # General / Voice Errors
    'must_join_voice': {
        'en': '❌ You must join a voice channel first to use this command!',
        'zh-TW': '❌ 你必須先加入一個語音頻道才能使用此指令！',
        'zh-CN': '❌ 你必须先加入一个语音频道才能使用此命令！',
        'ja': '❌ このコマンドを使用するには、まずボイスチャンネルに参加してください！',
        'ko': '❌ 이 명령어를 사용하려면 먼저 음성 채널에 참여해야 합니다!',
        'es': '❌ ¡Debes unirte a un canal de voz primero para usar este comando!'
    },
    'no_bot_in_room': {
        'en': '❌ No music bot is currently in your voice room!',
        'zh-TW': '❌ 你所在的語音房內目前沒有任何音樂機器人！',
        'zh-CN': '❌ 你所在的语音房内当前没有任何音乐机器人！',
        'ja': '❌ あなたのボイスチャンネルに現在音楽ボットはいません！',
        'ko': '❌ 현재 음성 방에 음악 봇이 없습니다!',
        'es': '❌ ¡No hay ningún bot de música en tu sala de voz!'
    },
    'no_bot_available': {
        'en': '⚠️ All music bots are currently playing in other voice rooms!\nPlease join an existing room or wait for a bot to finish, or use `/stop force_all: True` to reset.',
        'zh-TW': '⚠️ 目前此伺服器所有音樂機器人都在其他語音房為聽眾播放中！\n請加入現有房間一起聆聽，或等待其中一隻播放完畢，亦可使用 `/stop 全部退出: 是` 強制重置！',
        'zh-CN': '⚠️ 当前此服务器所有音乐机器人都在其他语音房播放中！\n请加入现有房间一起收听，或等待播放完毕，亦可使用 `/stop 全部退出: 是` 强制重置！',
        'ja': '⚠️ 現在すべての音楽ボットが他のボイスチャンネルで再生中です！\n既存の部屋に参加するか、`/stop force_all: True` でリセットしてください。',
        'ko': '⚠️ 현재 모든 음악 봇이 다른 음성 채널에서 재생 중입니다!\n기존 방에 참여하거나 `/stop force_all: True` 로 재설정하세요.',
        'es': '⚠️ ¡Todos los bots de música están ocupados en otras salas!\nÚnete a una sala existente o usa `/stop force_all: True` para restablecer.'
    },
    'only_one_bot_busy': {
        'en': '⚠️ Currently, only 1 bot 【{bot_name}】 is in this server and playing in {room_name}!\n\n💡 **Want multi-room playback?**\nInvite the remaining bots to this server using OAuth2 invite URLs!\n*(Or use `/stop force_all: True` to free the bot)*',
        'zh-TW': '⚠️ 目前此伺服器內僅有 1 隻機器人【{bot_name}】，且正在 {room_name} 為其他聽眾播放中！\n\n💡 **想解鎖多個語音房同時播歌？**\n請管理員點擊其他機器人的邀請連結進群，即可支援多房間同時獨立播放！\n*(亦可使用 `/stop 全部退出: 是` 強制釋放)*',
        'zh-CN': '⚠️ 当前此服务器内仅有 1 台机器人【{bot_name}】，且正在 {room_name} 为其他听众播放中！\n\n💡 **想解锁多个语音房同时播歌？**\n请管理员将其他机器人邀请进群，即可支持多房间同时独立播放！\n*(亦可使用 `/stop 全部退出: 是` 强制释放)*',
        'ja': '⚠️ 現在このサーバーにはボット【{bot_name}】が1台のみで、{room_name} で再生中です！\n\n💡 **複数部屋で同時に再生したいですか？**\n他のボットを招待してください！\n*(または `/stop force_all: True` でリセット)*',
        'ko': '⚠️ 현재 이 서버에는 1대의 봇【{bot_name}】만 있으며 {room_name} 에서 재생 중입니다!\n\n💡 **여러 방에서 동시 재생을 원하시나요?**\n다른 봇을 서버에 초대하세요!\n*(또는 `/stop force_all: True` 로 초기화)*',
        'es': '⚠️ ¡Actualmente solo hay 1 bot 【{bot_name}】 en este servidor y está reproduciendo en {room_name}!\n\n💡 **¿Quieres reproducción en varias salas?**\n¡Invita a los bots restantes!\n*(O usa `/stop force_all: True` para liberarlo)*'
    },
    'bot_not_in_guild': {
        'en': '❌ Bot 【{bot_name}】 has not been invited to this server yet!',
        'zh-TW': '❌ 機器人 【{bot_name}】 尚未加入此伺服器！',
        'zh-CN': '❌ 机器人 【{bot_name}】 尚未加入此服务器！',
        'ja': '❌ ボット【{bot_name}】はまだこのサーバーに参加していません！',
        'ko': '❌ 봇【{bot_name}】이(가) 아직 이 서버에 참여하지 않았습니다!',
        'es': '❌ ¡El bot 【{bot_name}】 aún no ha sido invitado a este servidor!'
    },
    'connect_failed': {
        'en': '❌ 【{bot_name}】 Failed to connect to voice channel: `{error}`',
        'zh-TW': '❌ 【{bot_name}】 連接語音頻道失敗: `{error}`',
        'zh-CN': '❌ 【{bot_name}】 连接语音频道失败: `{error}`',
        'ja': '❌ 【{bot_name}】 ボイスチャンネルへの接続に失敗しました: `{error}`',
        'ko': '❌ 【{bot_name}】 음성 채널 연결 실패: `{error}`',
        'es': '❌ 【{bot_name}】 Error al conectar al canal de voz: `{error}`'
    },

    # Playback / Queue
    'song_queued': {
        'en': '📥 【{bot_name}】 Added to Queue',
        'zh-TW': '📥 【{bot_name}】 已加入待播歌單',
        'zh-CN': '📥 【{bot_name}】 已加入待播歌单',
        'ja': '📥 【{bot_name}】 キューに追加されました',
        'ko': '📥 【{bot_name}】 재생 대기열에 추가됨',
        'es': '📥 【{bot_name}】 Añadido a la cola'
    },
    'song_dispatched': {
        'en': '🔍 Found **{title}**, dispatched **【{bot_name}】** to {channel} to play for you!',
        'zh-TW': '🔍 搜尋到 **{title}**，已派遣 **【{bot_name}】** 前往 {channel} 為您播放！',
        'zh-CN': '🔍 搜索到 **{title}**，已派遣 **【{bot_name}】** 前往 {channel} 为您播放！',
        'ja': '🔍 **{title}** を検索しました。**【{bot_name}】** を {channel} に派遣して再生を開始します！',
        'ko': '🔍 **{title}** 을(를) 찾았습니다. **【{bot_name}】** 을(를) {channel} 로 파견하여 재생합니다!',
        'es': '🔍 ¡Se encontró **{title}**, enviado **【{bot_name}】** a {channel} para reproducir!'
    },
    'song_started': {
        'en': '🔍 Found **{title}**, 【{bot_name}】 started playback!',
        'zh-TW': '🔍 搜尋到 **{title}**，【{bot_name}】開始播放！',
        'zh-CN': '🔍 搜索到 **{title}**，【{bot_name}】开始播放！',
        'ja': '🔍 **{title}** を検索しました。【{bot_name}】が再生を開始します！',
        'ko': '🔍 **{title}** 을(를) 찾았습니다. 【{bot_name}】 재생 시작!',
        'es': '🔍 ¡Se encontró **{title}**, 【{bot_name}】 comenzó a reproducir!'
    },
    'playlist_imported': {
        'en': '📑 【{bot_name}】 Successfully imported playlist!',
        'zh-TW': '📑 【{bot_name}】 成功匯入播放清單！',
        'zh-CN': '📑 【{bot_name}】 成功导入播放列表！',
        'ja': '📑 【{bot_name}】 プレイリストを正常にインポートしました！',
        'ko': '📑 【{bot_name}】 재생목록을 성공적으로 가져왔습니다!',
        'es': '📑 【{bot_name}】 ¡Lista de reproducción importada con éxito!'
    },
    'no_results': {
        'en': '❌ No search results found. Please try different keywords!',
        'zh-TW': '❌ 搜尋結果為空，請更換關鍵字！',
        'zh-CN': '❌ 搜索结果为空，请更换关键词！',
        'ja': '❌ 検索結果が見つかりませんでした。別のキーワードをお試しください！',
        'ko': '❌ 검색 결과가 없습니다. 다른 검색어로 시도해 주세요!',
        'es': '❌ No se encontraron resultados. ¡Intenta con otras palabras clave!'
    },
    'stream_failed': {
        'en': '⚠️ 【{bot_name}】 Track **{title}** unavailable or blocked, skipping to next...',
        'zh-TW': '⚠️ 【{bot_name}】 歌曲 **{title}** 因版權或已失效無法串流，跳至下一首...',
        'zh-CN': '⚠️ 【{bot_name}】 歌曲 **{title}** 因版权或失效无法串流，跳至下一首...',
        'ja': '⚠️ 【{bot_name}】 **{title}** は利用できないため、次の曲にスキップします...',
        'ko': '⚠️ 【{bot_name}】 **{title}** 을(를) 스트리밍할 수 없어 다음 곡으로 건너뜁니다...',
        'es': '⚠️ 【{bot_name}】 La pista **{title}** no está disponible, saltando a la siguiente...'
    },
    'queue_finished': {
        'en': '🎵 【{bot_name}】 All songs in queue have finished playing!',
        'zh-TW': '🎵 【{bot_name}】 歌單中的歌曲已全部播放完畢！',
        'zh-CN': '🎵 【{bot_name}】 播放列表中的歌曲已全部播放完毕！',
        'ja': '🎵 【{bot_name}】 キュー内のすべての曲の再生が終了しました！',
        'ko': '🎵 【{bot_name}】 대기열의 모든 곡 재생이 완료되었습니다!',
        'es': '🎵 【{bot_name}】 ¡Todas las canciones de la cola han terminado!'
    },

    # Controls
    'skipped': {
        'en': '⏭️ 【{bot_name}】 Skipped current track!',
        'zh-TW': '⏭️ 【{bot_name}】 已跳過當前歌曲！',
        'zh-CN': '⏭️ 【{bot_name}】 已跳过当前歌曲！',
        'ja': '⏭️ 【{bot_name}】 現在の曲をスキップしました！',
        'ko': '⏭️ 【{bot_name}】 현재 곡을 건너뛰었습니다!',
        'es': '⏭️ 【{bot_name}】 ¡Pista actual omitida!'
    },
    'stopped': {
        'en': '⏹️ 【{bot_name}】 Stopped playback and cleared queue!',
        'zh-TW': '⏹️ 【{bot_name}】 已停止播放並清空歌單！',
        'zh-CN': '⏹️ 【{bot_name}】 已停止播放并清空歌单！',
        'ja': '⏹️ 【{bot_name}】 再生を停止し、キューをクリアしました！',
        'ko': '⏹️ 【{bot_name}】 재생을 중지하고 대기열을 비웠습니다!',
        'es': '⏹️ 【{bot_name}】 ¡Reproducción detenida y cola borrada!'
    },
    'stopped_and_left': {
        'en': '⏹️ 【{bot_name}】 Stopped playback, cleared queue, and left voice channel!',
        'zh-TW': '⏹️ 【{bot_name}】 已停止播放、清空歌單並退出語音房！',
        'zh-CN': '⏹️ 【{bot_name}】 已停止播放、清空歌单并退出语音房！',
        'ja': '⏹️ 【{bot_name}】 再生を停止し、キューを消去して退出しました！',
        'ko': '⏹️ 【{bot_name}】 재생을 중지하고 대기열을 비운 뒤 음성 방에서 나갔습니다!',
        'es': '⏹️ 【{bot_name}】 ¡Reproducción detenida, cola borrada y desconectado!'
    },
    'force_stopped_all': {
        'en': '⏹️ Force reset and disconnected nodes: {names}! All bots are now on standby!',
        'zh-TW': '⏹️ 已強制重置並退出以下節點：{names}！所有機器人已釋放並隨時待命！',
        'zh-CN': '⏹️ 已强制重置并退出以下节点：{names}！所有机器人已释放并随时待命！',
        'ja': '⏹️ 次のノードを強制リセットして切断しました: {names}！すべてのボットが待機中です！',
        'ko': '⏹️ 다음 노드를 강제 재설정 및 퇴장시켰습니다: {names}! 모든 봇이 대기 중입니다!',
        'es': '⏹️ ¡Reinicio forzado y desconectados los nodos: {names}! ¡Todos los bots están en espera!'
    },
    'no_bots_connected': {
        'en': 'ℹ️ No music bots are currently connected to any voice channels.',
        'zh-TW': 'ℹ️ 目前沒有任何音樂機器人連線在語音房中。',
        'zh-CN': 'ℹ️ 当前没有任何音乐机器人连接在语音房中。',
        'ja': 'ℹ️ 現在ボイスチャンネルに接続されているボットはありません。',
        'ko': 'ℹ️ 현재 음성 채널에 연결된 음악 봇이 없습니다.',
        'es': 'ℹ️ Ningún bot de música está conectado actualmente a un canal de voz.'
    },
    'paused': {
        'en': '⏸️ 【{bot_name}】 Playback paused!',
        'zh-TW': '⏸️ 【{bot_name}】 音樂播放已暫停！',
        'zh-CN': '⏸️ 【{bot_name}】 音乐播放已暂停！',
        'ja': '⏸️ 【{bot_name}】 再生を一時停止しました！',
        'ko': '⏸️ 【{bot_name}】 재생이 일시 정지되었습니다!',
        'es': '⏸️ 【{bot_name}】 ¡Reproducción pausada!'
    },
    'resumed': {
        'en': '▶️ 【{bot_name}】 Playback resumed!',
        'zh-TW': '▶️ 【{bot_name}】 音樂播放已繼續！',
        'zh-CN': '▶️ 【{bot_name}】 音乐播放已继续！',
        'ja': '▶️ 【{bot_name}】 再生を再開しました！',
        'ko': '▶️ 【{bot_name}】 재생이 다시 시작되었습니다!',
        'es': '▶️ 【{bot_name}】 ¡Reproducción reanudada!'
    },
    'loop_mode_updated': {
        'en': '【{bot_name}】 Loop mode: **{mode}**',
        'zh-TW': '【{bot_name}】 循環模式：**{mode}**',
        'zh-CN': '【{bot_name}】 循环模式：**{mode}**',
        'ja': '【{bot_name}】 ループモード: **{mode}**',
        'ko': '【{bot_name}】 반복 모드: **{mode}**',
        'es': '【{bot_name}】 Modo de repetición: **{mode}**'
    },
    'not_playing': {
        'en': 'Currently not playing any music!',
        'zh-TW': '目前沒有正在播放的歌曲！',
        'zh-CN': '当前没有正在播放的歌曲！',
        'ja': '現在再生中の曲はありません！',
        'ko': '현재 재생 중인 곡이 없습니다!',
        'es': '¡Actualmente no se está reproduciendo ninguna canción!'
    },

    # Embed Fields
    'field_duration': {
        'en': '⏱️ Duration',
        'zh-TW': '⏱️ 時長',
        'zh-CN': '⏱️ 时长',
        'ja': '⏱️ 再生時間',
        'ko': '⏱️ 재생 시간',
        'es': '⏱️ Duración'
    },
    'field_requester': {
        'en': '👤 Requester',
        'zh-TW': '👤 點播者',
        'zh-CN': '👤 点播者',
        'ja': '👤 リクエスター',
        'ko': '👤 요청자',
        'es': '👤 Solicitado por'
    },
    'field_node': {
        'en': '🤖 Audio Node',
        'zh-TW': '🤖 播放節點',
        'zh-CN': '🤖 播放节点',
        'ja': '🤖 ノード',
        'ko': '🤖 오디오 노드',
        'es': '🤖 Nodo de audio'
    },
    'field_queue_pos': {
        'en': '📍 Queue Position',
        'zh-TW': '📍 隊列位置',
        'zh-CN': '📍 队列位置',
        'ja': '📍 キュー位置',
        'ko': '📍 대기열 위치',
        'es': '📍 Posición en cola'
    },
    'field_bitrate': {
        'en': '📶 Bitrate',
        'zh-TW': '📶 位元率',
        'zh-CN': '📶 比特率',
        'ja': '📶 ビットレート',
        'ko': '📶 비트레이트',
        'es': '📶 Tasa de bits'
    },
    'field_engine': {
        'en': '⚡ Engine',
        'zh-TW': '⚡ 音訊引擎',
        'zh-CN': '⚡ 音频引擎',
        'ja': '⚡ エンジン',
        'ko': '⚡ 엔진',
        'es': '⚡ Motor'
    },
    'field_loop_mode': {
        'en': '🔁 Loop Mode',
        'zh-TW': '🔁 循環模式',
        'zh-CN': '🔁 循环模式',
        'ja': '🔁 ループモード',
        'ko': '🔁 반복 모드',
        'es': '🔁 Modo de repetición'
    },
    'field_volume': {
        'en': '🔊 Volume',
        'zh-TW': '🔊 音量',
        'zh-CN': '🔊 音量',
        'ja': '🔊 音量',
        'ko': '🔊 볼륨',
        'es': '🔊 Volumen'
    },
    'field_dsp_filter': {
        'en': '🎛️ DSP Filter',
        'zh-TW': '🎛️ DSP 濾鏡',
        'zh-CN': '🎛️ DSP 滤镜',
        'ja': '🎛️ DSP フィルター',
        'ko': '🎛️ DSP 필터',
        'es': '🎛️ Filtro DSP'
    },
    'field_bassboost': {
        'en': '💥 Bass Boost',
        'zh-TW': '💥 重低音',
        'zh-CN': '💥 重低音',
        'ja': '💥 低音ブースト',
        'ko': '💥 베이스 부스트',
        'es': '💥 Refuerzo de graves'
    },
    'field_queue_count': {
        'en': '📜 Queue',
        'zh-TW': '📜 待播隊列',
        'zh-CN': '📜 待播队列',
        'ja': '📜 キュー',
        'ko': '📜 대기열',
        'es': '📜 Cola'
    },
    'loop_off': {
        'en': '❌ Disabled',
        'zh-TW': '❌ 關閉',
        'zh-CN': '❌ 关闭',
        'ja': '❌ 無効',
        'ko': '❌ 끄기',
        'es': '❌ Desactivado'
    },
    'loop_single': {
        'en': '🔂 Single Track',
        'zh-TW': '🔂 單曲循環',
        'zh-CN': '🔂 单曲循环',
        'ja': '🔂 1曲ループ',
        'ko': '🔂 한 곡 반복',
        'es': '🔂 Repetir una'
    },
    'loop_queue': {
        'en': '🔁 Entire Queue',
        'zh-TW': '🔁 全歌單循環',
        'zh-CN': '🔁 全歌单循环',
        'ja': '🔁 全曲ループ',
        'ko': '🔁 전체 반복',
        'es': '🔁 Repetir todo'
    },
    'now_playing_title': {
        'en': '🎵 {node_name} Now Playing',
        'zh-TW': '🎵 {node_name} 正在播放',
        'zh-CN': '🎵 {node_name} 正在播放',
        'ja': '🎵 {node_name} 再生中',
        'ko': '🎵 {node_name} 지금 재생 중',
        'es': '🎵 {node_name} Reproduciendo ahora'
    },
    'queue_title': {
        'en': '📜 {node_name} — Current Queue',
        'zh-TW': '📜 {node_name} — 當前播放清單',
        'zh-CN': '📜 {node_name} — 当前播放列表',
        'ja': '📜 {node_name} — 現在のキュー',
        'ko': '📜 {node_name} — 현재 재생목록',
        'es': '📜 {node_name} — Cola de reproducción actual'
    },
    'queue_empty': {
        'en': 'No more songs left in queue!',
        'zh-TW': '歌單中沒有後續歌曲囉！',
        'zh-CN': '播放列表中没有后续歌曲了！',
        'ja': 'キューにこれ以上の曲はありません！',
        'ko': '대기열에 다음 곡이 없습니다!',
        'es': '¡No hay más canciones en la cola!'
    },
    'queue_more': {
        'en': '\n*...and {count} more song(s)*',
        'zh-TW': '\n*...以及另外 {count} 首歌曲*',
        'zh-CN': '\n*...以及另外 {count} 首歌曲*',
        'ja': '\n*...他 {count} 曲*',
        'ko': '\n*...외 {count} 개 곡*',
        'es': '\n*...y {count} canción(es) más*'
    },
    'tip_krisp': {
        'en': 'Node: {node_name} | 💡 Turn off Discord "Krisp Noise Suppression" for highest audio fidelity!',
        'zh-TW': '節點: {node_name} | 💡 聽歌時建議關閉個人 Discord「Krisp 降噪」以保留最高動態！',
        'zh-CN': '节点: {node_name} | 💡 听歌时建议关闭个人 Discord「Krisp 降噪」以保留最高音质！',
        'ja': 'ノード: {node_name} | 💡 最高の音質を得るため、Discordの「Krispノイズ抑制」をオフにしてください！',
        'ko': '노드: {node_name} | 💡 최고 음질을 위해 디스코드의 "Krisp 노이즈 억제"를 끄는 것을 권장합니다!',
        'es': 'Nodo: {node_name} | 💡 ¡Desactiva "Krisp" en Discord para obtener la máxima calidad de audio!'
    },
    'tip_bitrate_low': {
        'en': 'Node: {node_name} | ⚠️ Channel bitrate is low ({bitrate}k). Admins can use /bitrate to maximize!',
        'zh-TW': '節點: {node_name} | ⚠️ 房內位元率偏低({bitrate}k)，管理員可用 /bitrate 拉滿解鎖極致音質！',
        'zh-CN': '节点: {node_name} | ⚠️ 房间比特率偏低({bitrate}k)，管理员可用 /bitrate 拉满解锁极致音质！',
        'ja': 'ノード: {node_name} | ⚠️ ビットレートが低めです({bitrate}k)。管理者は /bitrate で最大化できます！',
        'ko': '노드: {node_name} | ⚠️ 채널 비트레이트가 낮습니다({bitrate}k). 관리자는 /bitrate 로 최대화할 수 있습니다!',
        'es': 'Nodo: {node_name} | ⚠️ La tasa de bits es baja ({bitrate}k). ¡Los administradores pueden usar /bitrate!'
    },

    # New Feature: Autoplay
    'autoplay_enabled': {
        'en': '📻 【{bot_name}】 Autoplay mode is now **ENABLED**! (Infinite smart recommendations when queue ends)',
        'zh-TW': '📻 【{bot_name}】 智慧自動接歌模式已 **開啟**！(歌單結束後自動推薦風格相似歌曲)',
        'zh-CN': '📻 【{bot_name}】 智能自动接歌模式已 **开启**！(播放列表结束后自动推荐相似歌曲)',
        'ja': '📻 【{bot_name}】 自動再生モードが **有効** になりました！（キュー終了時に自動推薦再生）',
        'ko': '📻 【{bot_name}】 자동 재생 모드가 **활성화** 되었습니다! (대기열 종료 시 유사곡 자동 추천)',
        'es': '📻 【{bot_name}】 ¡El modo de reproducción automática está **ACTIVADO**!'
    },
    'autoplay_disabled': {
        'en': '📻 【{bot_name}】 Autoplay mode is now **DISABLED**.',
        'zh-TW': '📻 【{bot_name}】 智慧自動接歌模式已 **關閉**。',
        'zh-CN': '📻 【{bot_name}】 智能自动接歌模式已 **关闭**。',
        'ja': '📻 【{bot_name}】 自動再生モードが **無効** になりました。',
        'ko': '📻 【{bot_name}】 자동 재생 모드가 **비활성화** 되었습니다.',
        'es': '📻 【{bot_name}】 El modo de reproducción automática está **DESACTIVADO**.'
    },
    'autoplay_next_song': {
        'en': '📻 **[Autoplay Radio]** Recommended next track: **[{title}]({url})**',
        'zh-TW': '📻 **【自動接歌電台】** 為您無縫接續推薦：**[{title}]({url})**',
        'zh-CN': '📻 **【自动接歌电台】** 为您无缝接续推荐：**[{title}]({url})**',
        'ja': '📻 **[自動再生ラジオ]** おすすめの曲を追加しました: **[{title}]({url})**',
        'ko': '📻 **[자동 재생 라디오]** 추천 곡을 이어서 재생합니다: **[{title}]({url})**',
        'es': '📻 **[Radio Auto]** Siguiente pista recomendada: **[{title}]({url})**'
    },

    # New Feature: 24/7 Mode
    'mode_247_enabled': {
        'en': '🛡️ 【{bot_name}】 24/7 Stay-in-Room mode is **ENABLED**! Bot will remain in voice channel permanently.',
        'zh-TW': '🛡️ 【{bot_name}】 24/7 常駐模式已 **開啟**！機器人將永久停留在語音房內待命。',
        'zh-CN': '🛡️ 【{bot_name}】 24/7 常驻模式已 **开启**！机器人将永久停留在语音房内待命。',
        'ja': '🛡️ 【{bot_name}】 24/7 常駐モードが **有効** になりました！ボットは常にチャンネルに待機します。',
        'ko': '🛡️ 【{bot_name}】 24/7 상주 모드가 **활성화** 되었습니다! 봇이 항상 채널에 대기합니다.',
        'es': '🛡️ 【{bot_name}】 ¡El modo 24/7 está **ACTIVADO**! El bot permanecerá en la sala indefinidamente.'
    },
    'mode_247_disabled': {
        'en': '🛡️ 【{bot_name}】 24/7 Stay-in-Room mode is **DISABLED**.',
        'zh-TW': '🛡️ 【{bot_name}】 24/7 常駐模式已 **關閉**。',
        'zh-CN': '🛡️ 【{bot_name}】 24/7 常驻模式已 **关闭**。',
        'ja': '🛡️ 【{bot_name}】 24/7 常駐モードが **無効** になりました。',
        'ko': '🛡️ 【{bot_name}】 24/7 상주 모드가 **비활성화** 되었습니다.',
        'es': '🛡️ 【{bot_name}】 El modo 24/7 está **DESACTIVADO**.'
    },

    # New Feature: Lyrics
    'lyrics_searching': {
        'en': '🔍 Searching lyrics for **{title}**...',
        'zh-TW': '🔍 正在搜尋 **{title}** 的即時歌詞...',
        'zh-CN': '🔍 正在搜索 **{title}** 的即时歌词...',
        'ja': '🔍 **{title}** の歌詞を検索中...',
        'ko': '🔍 **{title}** 가사 검색 중...',
        'es': '🔍 Buscando letras para **{title}**...'
    },
    'lyrics_not_found': {
        'en': '❌ No synchronized or plain lyrics found for **{title}**.',
        'zh-TW': '❌ 找不到歌曲 **{title}** 的動態或純文字歌詞！',
        'zh-CN': '❌ 未找到歌曲 **{title}** 的动态或纯文本歌词！',
        'ja': '❌ **{title}** の歌詞が見つかりませんでした。',
        'ko': '❌ **{title}** 의 가사를 찾을 수 없습니다.',
        'es': '❌ No se encontraron letras para **{title}**.'
    },
    'lyrics_title': {
        'en': '📜 Lyrics — {title}',
        'zh-TW': '📜 動態歌詞 — {title}',
        'zh-CN': '📜 动态歌词 — {title}',
        'ja': '📜 歌詞 — {title}',
        'ko': '📜 가사 — {title}',
        'es': '📜 Letras — {title}'
    },

    # Language Switch
    'language_updated': {
        'en': '🌐 Server default language set to: **{lang_name}**!',
        'zh-TW': '🌐 伺服器預設語言已更新為：**{lang_name}**！',
        'zh-CN': '🌐 服务器默认语言已更新为：**{lang_name}**！',
        'ja': '🌐 サーバーのデフォルト言語が **{lang_name}** に設定されました！',
        'ko': '🌐 서버 기본 언어가 **{lang_name}**(으)로 설정되었습니다!',
        'es': '🌐 ¡Idioma predeterminado del servidor establecido en: **{lang_name}**!'
    },

    # Status Dashboard
    'status_title': {
        'en': '🎛️ Multi-Room Music Bot Cluster — Live Dashboard',
        'zh-TW': '🎛️ 多房間音樂機器人矩陣 — 即時狀態總覽',
        'zh-CN': '🎛️ 多房间音乐机器人矩阵 — 实时状态总览',
        'ja': '🎛️ マルチルーム音楽ボットクラスター — リアルタイムダッシュボード',
        'ko': '🎛️ 멀티룸 음악 봇 클러스터 — 실시간 상태 대시보드',
        'es': '🎛️ Clúster de Bots de Música Multi-Sala — Panel en Vivo'
    },
    'status_desc': {
        'en': 'Total **{total}** node(s) configured, **{joined}** joined this server:',
        'zh-TW': '後台共配置 **{total}** 隻節點，此伺服器已加入 **{joined}** 隻：',
        'zh-CN': '后台共配置 **{total}** 个节点，此服务器已加入 **{joined}** 个：',
        'ja': 'バックエンド設定: **{total}** 台、このサーバーに参加中: **{joined}** 台:',
        'ko': '설정된 노드: **{total}** 개, 이 서버에 참여 중: **{joined}** 개:',
        'es': 'Total de **{total}** nodo(s) configurados, **{joined}** unidos a este servidor:'
    },
    'status_idle': {
        'en': '⚪ Idle / Standby',
        'zh-TW': '⚪ 空閒待命',
        'zh-CN': '⚪ 空闲待命',
        'ja': '⚪ 待機中',
        'ko': '⚪ 대기 중',
        'es': '⚪ En espera'
    },
    'status_playing': {
        'en': '▶️ Playing',
        'zh-TW': '▶️ 播放中',
        'zh-CN': '▶️ 播放中',
        'ja': '▶️ 再生中',
        'ko': '▶️ 재생 중',
        'es': '▶️ Reproduciendo'
    },
    'status_paused': {
        'en': '⏸️ Paused',
        'zh-TW': '⏸️ 暫停中',
        'zh-CN': '⏸️ 暂停中',
        'ja': '⏸️ 一時停止中',
        'ko': '⏸️ 일시 정지',
        'es': '⏸️ Pausado'
    },
    'status_unjoined': {
        'en': '⚠️ Not invited to this server',
        'zh-TW': '⚠️ 未加入此伺服器',
        'zh-CN': '⚠️ 未加入此服务器',
        'ja': '⚠️ サーバー未参加',
        'ko': '⚠️ 서버 미참여',
        'es': '⚠️ No invitado a este servidor'
    },
    'status_unjoined_desc': {
        'en': 'Admin must invite this bot via OAuth2 URL for multi-room dispatching.',
        'zh-TW': '需由管理員透過 OAuth2 邀請連結拉進伺服器方可分流服務',
        'zh-CN': '需由管理员通过 OAuth2 邀请链接拉进服务器方可分流服务',
        'ja': 'マルチルーム対応のため、管理者がOAuth2で招待する必要があります。',
        'ko': '멀티룸 분배를 위해 관리자가 OAuth2 URL로 초대해야 합니다.',
        'es': 'El administrador debe invitarlo vía OAuth2 para servicio multi-sala.'
    },

    # Bitrate / Audio
    'bitrate_title': {
        'en': '🎚️ Voice Channel Bitrate Updated',
        'zh-TW': '🎚️ 語音房音質位元率已更新',
        'zh-CN': '🎚️ 语音房音质比特率已更新',
        'ja': '🎚️ ボイスチャンネルのビットレートが更新されました',
        'ko': '🎚️ 음성 채널 비트레이트 업데이트됨',
        'es': '🎚️ Tasa de bits del canal actualizada'
    },
    'volume_updated': {
        'en': '🔊 【{bot_name}】 Volume set to: **{vol}%**',
        'zh-TW': '🔊 【{bot_name}】 音量已設為：**{vol}%**',
        'zh-CN': '🔊 【{bot_name}】 音量已设为：**{vol}%**',
        'ja': '🔊 【{bot_name}】 音量を **{vol}%** に設定しました',
        'ko': '🔊 【{bot_name}】 볼륨이 **{vol}%** 로 설정되었습니다',
        'es': '🔊 【{bot_name}】 ¡Volumen establecido en: **{vol}%**!'
    }
}


def resolve_locale(
    interaction: Optional[discord.Interaction] = None,
    guild_id: Optional[int] = None,
    preferred_lang: Optional[str] = None
) -> str:
    """
    Intelligently resolves the target language in priority order:
    1. Explicit preferred_lang
    2. Guild custom setting in GUILD_LANGUAGES
    3. Interaction user locale (from Discord client)
    4. Default fallback (DEFAULT_LANGUAGE)
    """
    if preferred_lang and preferred_lang in SUPPORTED_LANGUAGES:
        return preferred_lang

    gid = guild_id or (interaction.guild_id if interaction else None)
    if gid and gid in GUILD_LANGUAGES:
        return GUILD_LANGUAGES[gid]

    if interaction and hasattr(interaction, 'locale') and interaction.locale:
        loc_str = str(interaction.locale).lower()
        if 'zh-tw' in loc_str or 'zh-hk' in loc_str or 'zh-mo' in loc_str:
            return 'zh-TW'
        if 'zh' in loc_str:
            return 'zh-CN'
        if 'ja' in loc_str:
            return 'ja'
        if 'ko' in loc_str:
            return 'ko'
        if 'es' in loc_str:
            return 'es'
        if 'en' in loc_str:
            return 'en'

    return DEFAULT_LANGUAGE


def t(
    key: str,
    interaction: Optional[discord.Interaction] = None,
    guild_id: Optional[int] = None,
    lang: Optional[str] = None,
    **kwargs: Any
) -> str:
    """
    Translates a key into the resolved language, supporting string formatting.
    """
    target_lang = resolve_locale(interaction=interaction, guild_id=guild_id, preferred_lang=lang)
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key

    text = entry.get(target_lang) or entry.get('en') or entry.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
