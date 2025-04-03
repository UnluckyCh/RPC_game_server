import threading
import time
from xmlrpc.server import SimpleXMLRPCServer
from socketserver import ThreadingMixIn

# -------------------------------
# Глобальные переменные и константы
# -------------------------------
PLAYER_ID_COUNTER = 1
SESSION_ID_COUNTER = 1

# Словарь зарегистрированных игроков: {player_id: {'id': player_id, 'name': name, 'session': session_id or None}}
players = {}

# Лобби – список player_id, ожидающих начала игры
lobby = []
lobby_lock = threading.Lock()

# Сессии игры: {session_id: GameSession instance}
sessions = {}

# Предопределённый список городов (все в нижнем регистре)
CITY_LIST = {
    "москва", "архангельск", "казань", "новосибирск", "владивосток",
    "ростов", "сочи", "самара", "питер", "екатеринбург",
    "уфа", "омск", "нижний новгород", "волгоград", "краснодар"
}

def get_last_significant_letter(city):
    """
    Возвращает последнюю значимую букву города.
    Если город заканчивается на 'ь', берётся предпоследняя буква.
    """
    city = city.strip().lower()
    if not city:
        return ''
    if city[-1] == 'ь' and len(city) > 1:
        return city[-2]
    return city[-1]

# -------------------------------
# Класс игровой сессии
# -------------------------------
class GameSession:
    def __init__(self, session_id, players_list):
        self.session_id = session_id
        self.players = players_list  # список игроков (каждый — dict)
        self.used_cities = set()
        self.current_city = None
        self.current_turn_index = 0
        self.condition = threading.Condition()  # для ожидания хода
        self.current_move = None  # ход, поданный текущим игроком
        self.game_over = False
        self.eliminated = []  # список исключённых игроков (их имена)

    def run(self):
        """
        Игровой цикл сессии.
        Если игрок не сделал ход вовремя или совершил ошибку (город не из списка,
        повтор или неверная начальная буква), он исключается и его имя записывается.
        Игра завершается, когда остаётся один игрок.
        """
        while len(self.players) > 1:
            current_player = self.players[self.current_turn_index]
            print(f"[Session {self.session_id}] Сейчас ход: {current_player['name']}")
            with self.condition:
                self.current_move = None
                self.condition.notify_all()  # уведомляем клиентов об изменении состояния
                self.condition.wait(timeout=30)  # ждем хода до 30 сек
                move = self.current_move

            if move is None:
                # Таймаут – игрок не подал ход
                print(f"[Session {self.session_id}] {current_player['name']} не успел сделать ход (таймаут). Исключаем.")
                self.eliminated.append(current_player['name'])
                self.players.pop(self.current_turn_index)
                if len(self.players) == 0:
                    break
                if self.current_turn_index >= len(self.players):
                    self.current_turn_index = 0
                continue

            move = move.strip().lower()
            # Проверка: город должен быть в списке
            if move not in CITY_LIST:
                print(f"[Session {self.session_id}] {current_player['name']} подал неверный город '{move}'. Исключаем.")
                self.eliminated.append(current_player['name'])
                self.players.pop(self.current_turn_index)
                if len(self.players) == 0:
                    break
                if self.current_turn_index >= len(self.players):
                    self.current_turn_index = 0
                continue

            # Проверка: город не должен повторяться
            if move in self.used_cities:
                print(f"[Session {self.session_id}] Город '{move}' уже был назван. Исключаем {current_player['name']}.")
                self.eliminated.append(current_player['name'])
                self.players.pop(self.current_turn_index)
                if len(self.players) == 0:
                    break
                if self.current_turn_index >= len(self.players):
                    self.current_turn_index = 0
                continue

            # Если это не первый ход, проверяем соответствие первой буквы
            if self.current_city is not None:
                required_letter = get_last_significant_letter(self.current_city)
                if move[0] != required_letter:
                    print(f"[Session {self.session_id}] Ход '{move}' не начинается с буквы '{required_letter.upper()}'. Исключаем {current_player['name']}.")
                    self.eliminated.append(current_player['name'])
                    self.players.pop(self.current_turn_index)
                    if len(self.players) == 0:
                        break
                    if self.current_turn_index >= len(self.players):
                        self.current_turn_index = 0
                    continue

            # Если всё корректно – принимаем ход
            self.used_cities.add(move)
            self.current_city = move
            print(f"[Session {self.session_id}] Принят ход '{move}' от {current_player['name']}.")
            self.current_turn_index = (self.current_turn_index + 1) % len(self.players)

        # Завершаем игру
        winner = self.players[0]['name'] if self.players else None
        print(f"[Session {self.session_id}] Игра окончена.")
        if self.eliminated:
            print(f"[Session {self.session_id}] Выбыли: {', '.join(self.eliminated)}")
        if winner:
            print(f"[Session {self.session_id}] Победитель: {winner}.")
        else:
            print(f"[Session {self.session_id}] Победителя нет.")
        self.game_over = True

# -------------------------------
# XMLRPC сервер (многопоточный)
# -------------------------------
class ThreadedXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer):
    pass

# -------------------------------
# RPC-методы для взаимодействия с клиентами
# -------------------------------
class GameServerRPC:
    def register_player(self, name):
        """
        Регистрирует нового игрока.
        Возвращает уникальный player_id.
        После регистрации игрок добавляется в лобби.
        """
        global PLAYER_ID_COUNTER
        player_id = PLAYER_ID_COUNTER
        PLAYER_ID_COUNTER += 1
        players[player_id] = {'id': player_id, 'name': name, 'session': None}
        with lobby_lock:
            lobby.append(player_id)
        print(f"[Server] Зарегистрирован игрок: {name} (id={player_id}).")
        attempt_create_session()
        return player_id

    def get_game_update(self, player_id):
        """
        Клиент запрашивает обновления игры.
        Если игрок ещё в лобби – возвращается статус ожидания.
        Если игрок уже в сессии, возвращается состояние сессии.
        При завершении игры возвращается информация о победителе и выбытии игроков.
        """
        if player_id not in players:
            return {"error": "Игрок не зарегистрирован."}
        player = players[player_id]
        session_id = player.get('session')
        if session_id is None:
            return {"status": "waiting", "message": "Ожидание подключения к игре..."}
        session = sessions.get(session_id)
        if session is None:
            return {"error": "Сессия не найдена."}
        if session.game_over:
            winner = session.players[0]['name'] if session.players else None
            return {
                "status": "game_over",
                "message": "Игра окончена.",
                "winner": winner,
                "eliminated": session.eliminated
            }
        current_player = session.players[session.current_turn_index] if session.players else None
        update = {
            "status": "in_game",
            "session_id": session_id,
            "current_city": session.current_city,
            "your_turn": (current_player and current_player['id'] == player_id),
            "players": [p['name'] for p in session.players]
        }
        return update

    def submit_move(self, player_id, move):
        """
        Клиент отправляет свой ход.
        Если это действительно его очередь, ход записывается в игровую сессию.
        """
        if player_id not in players:
            return "Игрок не зарегистрирован."
        player = players[player_id]
        session_id = player.get('session')
        if session_id is None:
            return "Вы не подключены к игровой сессии."
        session = sessions.get(session_id)
        if session is None:
            return "Сессия не найдена."
        with session.condition:
            current_player = session.players[session.current_turn_index]
            if current_player['id'] != player_id:
                return "Сейчас не ваш ход."
            session.current_move = move.strip().lower()
            session.condition.notify_all()
        return f"Ваш ход '{move}' принят."

# -------------------------------
# Функция формирования игровой сессии из лобби
# -------------------------------
def attempt_create_session():
    global SESSION_ID_COUNTER
    session_player_ids = []
    with lobby_lock:
        if len(lobby) >= 3:
            num_players = min(5, len(lobby))
            for _ in range(num_players):
                session_player_ids.append(lobby.pop(0))
    if session_player_ids:
        session_players = [players[pid] for pid in session_player_ids]
        session_id = SESSION_ID_COUNTER
        SESSION_ID_COUNTER += 1
        for p in session_players:
            p['session'] = session_id
        session = GameSession(session_id, session_players)
        sessions[session_id] = session
        print(f"[Server] Запущена игровая сессия {session_id} с игроками: {[p['name'] for p in session_players]}")
        threading.Thread(target=session.run, daemon=True).start()

# -------------------------------
# Запуск XMLRPC сервера
# -------------------------------
if __name__ == "__main__":
    server = ThreadedXMLRPCServer(("0.0.0.0", 8000), allow_none=True)
    server.register_instance(GameServerRPC())
    print("[Server] XMLRPC сервер запущен на 0.0.0.0:8000")
    server.serve_forever()
