import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'net/udp_discovery.dart';

class World {
  final int id;
  final String name;
  final String description;
  World({required this.id, required this.name, this.description = ''});
  factory World.fromJson(Map<String, dynamic> j) => World(
    id: j['id'] as int,
    name: (j['name'] ?? '') as String,
    description: (j['description'] ?? '') as String,
  );
}

class Place {
  final int id;
  final int? worldId;
  final String name;
  final String kind;
  final String description;
  final String atmosphere;
  final String rules;
  final List<String> tags;

  Place({
    required this.id,
    this.worldId,
    required this.name,
    this.kind = 'ambiente',
    this.description = '',
    this.atmosphere = '',
    this.rules = '',
    this.tags = const [],
  });

  factory Place.fromJson(Map<String, dynamic> j) => Place(
    id: j['id'] as int,
    worldId: j['world_id'] as int?,
    name: (j['name'] ?? '') as String,
    kind: (j['kind'] ?? 'ambiente') as String,
    description: (j['description'] ?? '') as String,
    atmosphere: (j['atmosphere'] ?? '') as String,
    rules: (j['rules'] ?? '') as String,
    tags: ((j['tags'] as List?) ?? const []).map((e) => e.toString()).toList(),
  );
}

class WorldVoice {
  final int id;
  final int? sessionId;
  final int turn;
  final String kind;
  final String title;
  final String author;
  final String body;
  final List<String> claims;
  final String truthStatus;
  final List<String> knownBy;
  final String visibility;
  final String source;
  final double createdAt;

  WorldVoice({
    required this.id,
    this.sessionId,
    this.turn = 0,
    this.kind = 'rumor',
    this.title = '',
    this.author = '',
    this.body = '',
    this.claims = const [],
    this.truthStatus = 'incerto',
    this.knownBy = const [],
    this.visibility = 'publico',
    this.source = '',
    this.createdAt = 0,
  });

  factory WorldVoice.fromJson(Map<String, dynamic> j) => WorldVoice(
    id: (j['id'] as num?)?.toInt() ?? 0,
    sessionId: (j['session_id'] as num?)?.toInt(),
    turn: (j['turn'] as num?)?.toInt() ?? 0,
    kind: (j['kind'] ?? 'rumor') as String,
    title: (j['title'] ?? '') as String,
    author: (j['author'] ?? '') as String,
    body: (j['body'] ?? '') as String,
    claims: ((j['claims'] as List?) ?? const [])
        .map((e) => e.toString())
        .toList(),
    truthStatus: (j['truth_status'] ?? 'incerto') as String,
    knownBy: ((j['known_by'] as List?) ?? const [])
        .map((e) => e.toString())
        .toList(),
    visibility: (j['visibility'] ?? 'publico') as String,
    source: (j['source'] ?? '') as String,
    createdAt: ((j['created_at'] ?? 0) as num).toDouble(),
  );
}

class Character {
  final int id;
  final String kind;
  final String name;
  final String avatar;
  final String tagline;
  final String descriptionPublic;
  final String persona;
  final String greeting;
  final String appearance;
  final String personality;
  final String backstory;
  final String speechStyle;
  final String likes;
  final String dislikes;
  final String fears;
  final String relationship;
  final String secrets;
  final String exampleDialogue;
  final List<String> tags;
  final int? worldId;
  final String model;
  final String voice;
  final Map<String, String> portraits;
  final String visualPrompt;
  final int artSeed;
  final String visualStyle;
  final Map<String, int> attributes;
  final Map<String, int> skills;
  final bool archived;

  Character({
    required this.id,
    required this.kind,
    required this.name,
    this.avatar = '',
    this.tagline = '',
    this.descriptionPublic = '',
    this.persona = '',
    this.greeting = '',
    this.appearance = '',
    this.personality = '',
    this.backstory = '',
    this.speechStyle = '',
    this.likes = '',
    this.dislikes = '',
    this.fears = '',
    this.relationship = '',
    this.secrets = '',
    this.exampleDialogue = '',
    this.tags = const [],
    this.worldId,
    this.model = '',
    this.voice = '',
    this.portraits = const {},
    this.visualPrompt = '',
    this.artSeed = 0,
    this.visualStyle = 'anime',
    this.attributes = const {},
    this.skills = const {},
    this.archived = false,
  });

  factory Character.fromJson(Map<String, dynamic> j) => Character(
    id: j['id'] as int,
    kind: (j['kind'] ?? 'npc') as String,
    name: (j['name'] ?? '') as String,
    avatar: (j['avatar'] ?? '') as String,
    tagline: (j['tagline'] ?? '') as String,
    descriptionPublic: (j['description_public'] ?? '') as String,
    persona: (j['persona'] ?? '') as String,
    greeting: (j['greeting'] ?? '') as String,
    appearance: (j['appearance'] ?? '') as String,
    personality: (j['personality'] ?? '') as String,
    backstory: (j['backstory'] ?? '') as String,
    speechStyle: (j['speech_style'] ?? '') as String,
    likes: (j['likes'] ?? '') as String,
    dislikes: (j['dislikes'] ?? '') as String,
    fears: (j['fears'] ?? '') as String,
    relationship: (j['relationship'] ?? '') as String,
    secrets: (j['secrets'] ?? '') as String,
    exampleDialogue: (j['example_dialogue'] ?? '') as String,
    tags: ((j['tags'] as List?) ?? const []).map((e) => e.toString()).toList(),
    worldId: j['world_id'] as int?,
    model: (j['model'] ?? '') as String,
    voice: (j['voice'] ?? '') as String,
    portraits: ((j['portraits'] as Map?) ?? const {}).map(
      (k, v) => MapEntry(k.toString(), v.toString()),
    ),
    visualPrompt: (j['visual_prompt'] ?? '') as String,
    artSeed: (j['art_seed'] as num?)?.toInt() ?? 0,
    visualStyle: (j['visual_style'] ?? 'anime') as String,
    attributes: ((j['attributes'] as Map?) ?? const {}).map(
      (k, v) => MapEntry(k.toString(), (v as num?)?.toInt() ?? 10),
    ),
    skills: ((j['skills'] as Map?) ?? const {}).map(
      (k, v) => MapEntry(k.toString(), (v as num?)?.toInt() ?? 0),
    ),
    archived: (j['archived'] as num?)?.toInt() == 1 || j['archived'] == true,
  );
}

class Session {
  final int id;
  final int characterId;
  final int? protagonistId;
  final int? placeId;
  final String title;
  final String summary;
  final String lastLine;
  final String sceneImage;
  final int turnCount;
  final double updatedAt;
  final List<int> castIds;
  final bool archived;

  Session({
    required this.id,
    required this.characterId,
    this.protagonistId,
    this.placeId,
    required this.title,
    this.summary = '',
    this.lastLine = '',
    this.sceneImage = '',
    this.turnCount = 0,
    this.updatedAt = 0,
    this.castIds = const [],
    this.archived = false,
  });

  factory Session.fromJson(Map<String, dynamic> j) => Session(
    id: j['id'] as int,
    characterId: j['character_id'] as int,
    protagonistId: j['protagonist_id'] as int?,
    placeId: (j['place_id'] as num?)?.toInt(),
    title: (j['title'] ?? '') as String,
    summary: (j['summary'] ?? '') as String,
    lastLine: (j['last_line'] ?? '') as String,
    sceneImage: (j['scene_image'] ?? '') as String,
    turnCount: (j['turn_count'] ?? 0) as int,
    updatedAt: ((j['updated_at'] ?? 0) as num).toDouble(),
    castIds: ((j['cast_ids'] as List?) ?? const [])
        .map((e) => (e as num).toInt())
        .toList(),
    archived: (j['archived'] as num?)?.toInt() == 1 || j['archived'] == true,
  );
}

class ManaInfo {
  final int balance;
  final bool dailyAvailable;
  final int dailyAmount;
  final int nextDailyIn;

  ManaInfo({
    required this.balance,
    this.dailyAvailable = false,
    this.dailyAmount = 40,
    this.nextDailyIn = 0,
  });

  factory ManaInfo.fromJson(Map<String, dynamic> j) => ManaInfo(
    balance: (j['balance'] as num?)?.toInt() ?? 0,
    dailyAvailable: j['daily_available'] == true,
    dailyAmount: (j['daily_amount'] as num?)?.toInt() ?? 40,
    nextDailyIn: (j['next_daily_in'] as num?)?.toInt() ?? 0,
  );
}

class CastEntry {
  final Character character;
  final int nr;
  final Map<String, int> axes;
  final String vinculo;
  final String mood;
  final String goal;
  final String status;

  CastEntry({
    required this.character,
    required this.nr,
    this.axes = const {},
    this.vinculo = '',
    this.mood = '',
    this.goal = '',
    this.status = '',
  });

  factory CastEntry.fromJson(Map<String, dynamic> j) => CastEntry(
    character: Character.fromJson(
      (j['character'] as Map?)?.cast<String, dynamic>() ?? const {},
    ),
    nr: (j['nr'] as num?)?.toInt() ?? 20,
    axes: ((j['axes'] as Map?) ?? const {}).map(
      (k, v) => MapEntry(k.toString(), (v as num).toInt()),
    ),
    vinculo: (j['vinculo'] ?? '') as String,
    mood: (j['mood'] ?? '') as String,
    goal: (j['goal'] ?? '') as String,
    status: (j['status'] ?? '') as String,
  );
}

class SessionCast {
  final Character? main;
  final int mainNr;
  final Map<String, int> mainAxes;
  final String mainVinculo;
  final List<CastEntry> cast;

  SessionCast({
    this.main,
    this.mainNr = 20,
    this.mainAxes = const {},
    this.mainVinculo = '',
    this.cast = const [],
  });

  factory SessionCast.fromJson(Map<String, dynamic> j) {
    final main = (j['main'] as Map?)?.cast<String, dynamic>();
    return SessionCast(
      main: main == null
          ? null
          : Character.fromJson(
              (main['character'] as Map?)?.cast<String, dynamic>() ?? const {},
            ),
      mainNr: (main?['nr'] as num?)?.toInt() ?? 20,
      mainAxes: ((main?['axes'] as Map?) ?? const {}).map(
        (k, v) => MapEntry(k.toString(), (v as num).toInt()),
      ),
      mainVinculo: (main?['vinculo'] ?? '') as String,
      cast: ((j['cast'] as List?) ?? const [])
          .map((e) => CastEntry.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
    );
  }
}

Map<String, dynamic>? _asJsonMap(Object? value) {
  if (value is! Map) return null;
  return value.map((k, v) => MapEntry(k.toString(), v));
}

List<Map<String, dynamic>>? _asJsonMapList(Object? value) {
  if (value is! List) return null;
  return value
      .whereType<Map>()
      .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
      .toList();
}

class ChatChunk {
  final String? delta;
  final String? replace;
  final String? status;
  final int? nr;
  final Map<String, int>? axes;
  final String? vinculo;
  final bool done;
  final Map<String, dynamic>? scene;
  final Map<String, dynamic>? pacing;
  final Map<String, dynamic>? commitments;
  final List<Map<String, dynamic>>? cast;
  final Map<String, dynamic>? resolution;
  final String? mood;

  ChatChunk({
    this.delta,
    this.replace,
    this.status,
    this.nr,
    this.axes,
    this.vinculo,
    this.done = false,
    this.scene,
    this.pacing,
    this.commitments,
    this.cast,
    this.resolution,
    this.mood,
  });
}

class Chapter {
  final int number;
  final String summary;
  Chapter({required this.number, required this.summary});
  factory Chapter.fromJson(Map<String, dynamic> j) => Chapter(
    number: (j['number'] ?? 0) as int,
    summary: (j['summary'] ?? '') as String,
  );
}

class Choice {
  final String text;
  final String intent;
  final String label;
  final bool risk;
  final int dc;
  final String difficulty;

  const Choice({
    required this.text,
    this.intent = '',
    this.label = 'livre',
    this.risk = false,
    this.dc = 0,
    this.difficulty = '',
  });

  factory Choice.fromJson(dynamic value) {
    if (value is String) return Choice(text: value);
    if (value is Map) {
      final j = value.map((k, v) => MapEntry(k.toString(), v));
      return Choice(
        text: (j['text'] ?? '').toString(),
        intent: (j['intent'] ?? '').toString(),
        label: (j['label'] ?? 'livre').toString(),
        risk: j['risk'] == true,
        dc: (j['dc'] as num?)?.toInt() ?? 0,
        difficulty: (j['difficulty'] ?? '').toString(),
      );
    }
    return const Choice(text: '');
  }
}

class ChatMessage {
  final int id;
  final String role;
  final String content;
  final Map<String, dynamic>? resolution;
  ChatMessage(this.id, this.role, this.content, {this.resolution});
  factory ChatMessage.fromJson(Map<String, dynamic> j) => ChatMessage(
    (j['id'] ?? 0) as int,
    (j['role'] ?? '') as String,
    (j['content'] ?? '') as String,
    resolution: _asJsonMap(j['resolution']),
  );
}

class ApiClient {
  static const int discoveryPort = 5112;
  static const int apkPort = 8000;
  static const String defaultServer = String.fromEnvironment(
    'LUMINUS_SERVER',
    defaultValue: 'http://192.168.5.33:5111',
  );
  static String baseUrl = defaultServer;
  static String adminKey = '';

  static Uri _u(String path) => Uri.parse('$baseUrl$path');
  static const _json = {'Content-Type': 'application/json'};

  static String _detail(
    Map<String, dynamic> data,
    int status,
    String fallback,
  ) {
    final detail = data['detail'];
    if (detail is String && detail.trim().isNotEmpty) return detail;
    return '$fallback (HTTP $status)';
  }

  static const List<String> _fallbackServers = [
    defaultServer,
    'http://100.64.7.30:5111',
  ];

  static Future<bool> _reachable(String url) async {
    try {
      final r = await http
          .get(Uri.parse('$url/api/ping'))
          .timeout(const Duration(milliseconds: 1500));
      return r.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<String> discover() async {
    final found = await discoverViaUdp();
    if (found != null) {
      final url = found.trim().replaceAll(RegExp(r'/+$'), '');
      if (await _reachable(url)) {
        baseUrl = url;
        return url;
      }
    }
    final candidates = <String>[baseUrl, ..._fallbackServers];
    final seen = <String>{};
    for (final raw in candidates) {
      final url = raw.trim().replaceAll(RegExp(r'/+$'), '');
      if (url.isEmpty || !seen.add(url)) continue;
      if (await _reachable(url)) {
        baseUrl = url;
        return url;
      }
    }
    return baseUrl;
  }

  static Uri apkUri(String path) {
    final base = Uri.parse(baseUrl);
    final clean = path.replaceFirst(RegExp(r'^/+'), '');
    return base.replace(scheme: 'http', port: apkPort, path: '/$clean');
  }

  static String mediaUrl(String path) {
    final value = path.trim();
    if (value.isEmpty) return '';
    if (value.startsWith('http://') || value.startsWith('https://')) {
      return value;
    }
    return value.startsWith('/') ? '$baseUrl$value' : '$baseUrl/$value';
  }

  static Future<Map<String, dynamic>> generateCover({
    int? characterId,
    required String name,
    required String kind,
    String appearance = '',
    String personality = '',
    String backstory = '',
    List<String> tags = const [],
    String world = '',
    String style = 'anime',
    int? seed,
  }) async {
    final r = await http
        .post(
          _u('/api/art/cover'),
          headers: _json,
          body: jsonEncode({
            'character_id': characterId,
            'name': name,
            'kind': kind,
            'appearance': appearance,
            'personality': personality,
            'backstory': backstory,
            'tags': tags,
            'world': world,
            'style': style,
            'seed': seed,
          }),
        )
        .timeout(const Duration(minutes: 10));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      final detail = data['detail'];
      throw detail is String && detail.isNotEmpty
          ? detail
          : 'Falha ao gerar capa (HTTP ${r.statusCode}).';
    }
    return data;
  }

  static Future<Map<String, dynamic>> generatePortraits(int characterId) async {
    final r = await http
        .post(_u('/api/characters/$characterId/portraits'))
        .timeout(const Duration(minutes: 10));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      final detail = data['detail'];
      throw detail is String && detail.isNotEmpty
          ? detail
          : 'Falha ao gerar retratos (HTTP ${r.statusCode}).';
    }
    return data;
  }

  static Future<Map<String, dynamic>> regenerateVisual(int characterId) async {
    final r = await http
        .post(_u('/api/characters/$characterId/visual'))
        .timeout(const Duration(minutes: 3));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      final detail = data['detail'];
      throw detail is String && detail.isNotEmpty
          ? detail
          : 'Falha ao criar identidade visual (HTTP ${r.statusCode}).';
    }
    return data;
  }

  static String friendly(Object error) {
    final text = error.toString().toLowerCase();
    if (text.contains('socket') ||
        text.contains('clientexception') ||
        text.contains('timeout') ||
        text.contains('timed out') ||
        text.contains('connection') ||
        text.contains('failed host lookup') ||
        text.contains('network')) {
      return 'Não consegui falar com o servidor. Confira o Wi-Fi e o endereço em Ajustes.';
    }
    return 'Algo deu errado. Tente de novo em instantes.';
  }

  static Future<Map<String, dynamic>> health() async {
    final ping = await http
        .get(_u('/api/ping'))
        .timeout(const Duration(seconds: 3));
    if (ping.statusCode != 200) {
      throw 'Servidor respondeu HTTP ${ping.statusCode}.';
    }
    final r = await http
        .get(_u('/api/health'))
        .timeout(const Duration(seconds: 20));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<Map<String, dynamic>> warmupStart() async {
    final r = await http
        .post(_u('/api/warmup'))
        .timeout(const Duration(seconds: 15));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<Map<String, dynamic>> warmupStatus() async {
    final r = await http
        .get(_u('/api/warmup'))
        .timeout(const Duration(seconds: 10));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<List<String>> voices() async {
    final r = await http
        .get(_u('/api/voices'))
        .timeout(const Duration(seconds: 10));
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    return ((d['voices'] as List?) ?? const [])
        .map((e) => e.toString())
        .toList();
  }

  static Future<List<World>> worlds() async {
    final r = await http
        .get(_u('/api/worlds'))
        .timeout(const Duration(seconds: 10));
    return (jsonDecode(r.body) as List)
        .map((e) => World.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<List<Place>> places({int? worldId}) async {
    final q = worldId == null ? '' : '?world_id=$worldId';
    final r = await http
        .get(_u('/api/places$q'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode >= 300) {
      throw _detail({}, r.statusCode, 'Falha ao carregar os ambientes.');
    }
    return ((jsonDecode(r.body) as List?) ?? const [])
        .map((e) => Place.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  static Future<Place> createPlace({
    int? worldId,
    required String name,
    String kind = 'ambiente',
    String description = '',
    String atmosphere = '',
    String rules = '',
    List<String> tags = const [],
  }) async {
    final r = await http
        .post(
          _u('/api/places'),
          headers: _json,
          body: jsonEncode({
            'world_id': worldId,
            'name': name,
            'kind': kind,
            'description': description,
            'atmosphere': atmosphere,
            'rules': rules,
            'tags': tags,
          }),
        )
        .timeout(const Duration(seconds: 10));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao criar o ambiente.');
    }
    return Place.fromJson(data);
  }

  static Future<Place> updatePlace(
    int id, {
    int? worldId,
    required String name,
    String kind = 'ambiente',
    String description = '',
    String atmosphere = '',
    String rules = '',
    List<String> tags = const [],
  }) async {
    final r = await http
        .put(
          _u('/api/places/$id'),
          headers: _json,
          body: jsonEncode({
            'world_id': worldId,
            'name': name,
            'kind': kind,
            'description': description,
            'atmosphere': atmosphere,
            'rules': rules,
            'tags': tags,
          }),
        )
        .timeout(const Duration(seconds: 10));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao salvar o ambiente.');
    }
    return Place.fromJson(data);
  }

  static Future<void> deletePlace(int id) async {
    final r = await http
        .delete(_u('/api/places/$id'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode >= 300) throw 'HTTP ${r.statusCode}';
  }

  static Future<World> createWorld(String name, String description) async {
    final r = await http
        .post(
          _u('/api/worlds'),
          headers: _json,
          body: jsonEncode({'name': name, 'description': description}),
        )
        .timeout(const Duration(seconds: 10));
    return World.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<List<WorldVoice>> sessionVoices(
    int sessionId, {
    int limit = 20,
  }) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/voices?limit=$limit'))
        .timeout(const Duration(seconds: 15));
    final data = (jsonDecode(r.body) as Map).cast<String, dynamic>();
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao carregar as vozes do mundo.');
    }
    return ((data['voices'] as List?) ?? const [])
        .map((e) => WorldVoice.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  static Future<WorldVoice> generateSessionVoice(int sessionId) async {
    final r = await http
        .post(_u('/api/sessions/$sessionId/voices'))
        .timeout(const Duration(minutes: 2));
    final data = (jsonDecode(r.body) as Map).cast<String, dynamic>();
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao gerar um artefato do mundo.');
    }
    return WorldVoice.fromJson(data);
  }

  static Future<String> exportWorldPack(
    int worldId, {
    bool includeSessions = true,
  }) async {
    final include = includeSessions ? 'true' : 'false';
    final r = await http
        .get(_u('/api/worlds/$worldId/export?include_sessions=$include'))
        .timeout(const Duration(minutes: 2));
    if (r.statusCode >= 300) {
      Map<String, dynamic> data = {};
      try {
        data = (jsonDecode(r.body) as Map).cast<String, dynamic>();
      } catch (_) {}
      throw _detail(data, r.statusCode, 'Falha ao exportar o mundo.');
    }
    return r.body;
  }

  static Future<Map<String, dynamic>> importWorldPack(String packJson) async {
    try {
      jsonDecode(packJson);
    } catch (_) {
      throw 'O arquivo escolhido não é um JSON válido.';
    }
    final r = await http
        .post(_u('/api/worlds/import'), headers: _json, body: packJson)
        .timeout(const Duration(minutes: 5));
    Map<String, dynamic> data = {};
    try {
      data = (jsonDecode(r.body) as Map).cast<String, dynamic>();
    } catch (_) {}
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao importar o mundo.');
    }
    return data;
  }

  static Future<List<Character>> characters({
    String? kind,
    bool includeArchived = false,
  }) async {
    final q = <String>[
      if (kind != null) 'kind=$kind',
      if (includeArchived) 'include_archived=1',
    ].join('&');
    final r = await http
        .get(_u('/api/characters${q.isEmpty ? '' : '?$q'}'))
        .timeout(const Duration(seconds: 10));
    return (jsonDecode(r.body) as List)
        .map((e) => Character.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<Character> createCharacter(Map<String, dynamic> body) async {
    final r = await http
        .post(_u('/api/characters'), headers: _json, body: jsonEncode(body))
        .timeout(const Duration(seconds: 15));
    return Character.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<Character> updateCharacter(
    int id,
    Map<String, dynamic> body,
  ) async {
    final r = await http
        .put(_u('/api/characters/$id'), headers: _json, body: jsonEncode(body))
        .timeout(const Duration(seconds: 15));
    return Character.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<Character> archiveCharacter(int id, bool archived) async {
    final r = await http
        .post(
          _u('/api/characters/$id/archive'),
          headers: _json,
          body: jsonEncode({'archived': archived}),
        )
        .timeout(const Duration(seconds: 10));
    return Character.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<void> deleteCharacter(int id) async {
    await http
        .delete(_u('/api/characters/$id'))
        .timeout(const Duration(seconds: 10));
  }

  static Future<Map<String, dynamic>> assistCharacter({
    required String seed,
    required String kind,
    String tone = '',
    String name = '',
    int? worldId,
  }) async {
    final r = await http
        .post(
          _u('/api/assist/character'),
          headers: _json,
          body: jsonEncode({
            'seed': seed,
            'kind': kind,
            'tone': tone,
            'name': name,
            'world_id': worldId,
          }),
        )
        .timeout(const Duration(seconds: 600));
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    if (d['error'] != null) throw 'IA não conseguiu gerar: ${d['error']}';
    return (d['draft'] as Map).cast<String, dynamic>();
  }

  static Future<Map<String, dynamic>> assistRefine({
    required Map<String, dynamic> draft,
    required String instruction,
    String kind = 'npc',
  }) async {
    final r = await http
        .post(
          _u('/api/assist/refine'),
          headers: _json,
          body: jsonEncode({
            'draft': draft,
            'instruction': instruction,
            'kind': kind,
          }),
        )
        .timeout(const Duration(seconds: 300));
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    if (d['error'] != null) throw 'IA não conseguiu refinar: ${d['error']}';
    return (d['draft'] as Map).cast<String, dynamic>();
  }

  static Future<List<Session>> allSessions({
    bool includeArchived = false,
  }) async {
    final q = includeArchived ? '?include_archived=1' : '';
    final r = await http
        .get(_u('/api/sessions$q'))
        .timeout(const Duration(seconds: 10));
    return (jsonDecode(r.body) as List)
        .map((e) => Session.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<ManaInfo> mana() async {
    final r = await http
        .get(_u('/api/mana'))
        .timeout(const Duration(seconds: 10));
    return ManaInfo.fromJson(
      (jsonDecode(r.body) as Map).cast<String, dynamic>(),
    );
  }

  static Future<ManaInfo> claimDailyMana() async {
    final r = await http
        .post(_u('/api/mana/daily'))
        .timeout(const Duration(seconds: 15));
    final data = (jsonDecode(r.body) as Map).cast<String, dynamic>();
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao resgatar a Mana diária.');
    }
    return ManaInfo.fromJson(data);
  }

  static Future<Session> session(int id) async {
    final r = await http
        .get(_u('/api/sessions/$id'))
        .timeout(const Duration(seconds: 10));
    final data = (jsonDecode(r.body) as Map).cast<String, dynamic>();
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'História não encontrada.');
    }
    return Session.fromJson(data);
  }

  static Future<Character> character(int id) async {
    final r = await http
        .get(_u('/api/characters/$id'))
        .timeout(const Duration(seconds: 10));
    final data = (jsonDecode(r.body) as Map).cast<String, dynamic>();
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Personagem não encontrado.');
    }
    return Character.fromJson(data);
  }

  static Future<List<Session>> sessions(
    int characterId, {
    bool includeArchived = false,
  }) async {
    final q = StringBuffer('?character_id=$characterId');
    if (includeArchived) q.write('&include_archived=1');
    final r = await http
        .get(_u('/api/sessions$q'))
        .timeout(const Duration(seconds: 10));
    return (jsonDecode(r.body) as List)
        .map((e) => Session.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<Session> createSession(
    int characterId, {
    int? protagonistId,
    int? placeId,
    String title = '',
    List<int> castIds = const [],
  }) async {
    final r = await http
        .post(
          _u('/api/sessions'),
          headers: _json,
          body: jsonEncode({
            'character_id': characterId,
            'protagonist_id': protagonistId,
            'place_id': placeId,
            'title': title,
            'cast_ids': castIds,
          }),
        )
        .timeout(const Duration(seconds: 10));
    if (r.statusCode >= 300) {
      throw _detail(
        jsonDecode(r.body) as Map<String, dynamic>,
        r.statusCode,
        'Falha ao iniciar a história.',
      );
    }
    return Session.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<SessionCast> sessionCast(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/cast'))
        .timeout(const Duration(seconds: 10));
    return SessionCast.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<Session> addCast(int sessionId, int characterId) async {
    final r = await http
        .post(
          _u('/api/sessions/$sessionId/cast'),
          headers: _json,
          body: jsonEncode({'character_id': characterId}),
        )
        .timeout(const Duration(seconds: 10));
    return Session.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<Session> removeCast(int sessionId, int characterId) async {
    final r = await http
        .delete(_u('/api/sessions/$sessionId/cast/$characterId'))
        .timeout(const Duration(seconds: 10));
    return Session.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<Session> archiveSession(int id, bool archived) async {
    final r = await http
        .post(
          _u('/api/sessions/$id/archive'),
          headers: _json,
          body: jsonEncode({'archived': archived}),
        )
        .timeout(const Duration(seconds: 10));
    return Session.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<void> deleteSession(int id) async {
    await http
        .delete(_u('/api/sessions/$id'))
        .timeout(const Duration(seconds: 10));
  }

  static Future<Map<String, dynamic>> generateScene(
    int sessionId, {
    String style = 'anime',
  }) async {
    final r = await http
        .post(
          _u('/api/sessions/$sessionId/scene'),
          headers: _json,
          body: jsonEncode({'style': style}),
        )
        .timeout(const Duration(minutes: 10));
    Map<String, dynamic> d;
    try {
      d = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      d = {};
    }
    if (r.statusCode >= 300) {
      final detail = d['detail'];
      throw detail is String && detail.isNotEmpty
          ? detail
          : 'Falha ao ilustrar a cena (HTTP ${r.statusCode}).';
    }
    return d;
  }

  static Future<Map<String, dynamic>> generateImage({
    required String prompt,
    String style = 'anime',
    int width = 1024,
    int height = 1024,
    bool enhance = true,
    bool adult = false,
    String model = '',
    int? seed,
  }) async {
    final r = await http
        .post(
          _u('/api/art/free'),
          headers: _json,
          body: jsonEncode({
            'prompt': prompt,
            'style': style,
            'width': width,
            'height': height,
            'enhance': enhance,
            'adult': adult,
            'model': model,
            'seed': ?seed,
          }),
        )
        .timeout(const Duration(minutes: 10));
    Map<String, dynamic> d;
    try {
      d = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      d = {};
    }
    if (r.statusCode >= 300) {
      final detail = d['detail'];
      throw detail is String && detail.isNotEmpty
          ? detail
          : 'Falha ao gerar a imagem (HTTP ${r.statusCode}).';
    }
    return d;
  }

  static Future<List<Map<String, dynamic>>> artGallery({int limit = 40}) async {
    final r = await http
        .get(_u('/api/art/gallery?limit=$limit'))
        .timeout(const Duration(seconds: 10));
    return (jsonDecode(r.body) as List)
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
  }

  static Future<void> deleteArt(String name) async {
    final r = await http
        .delete(
          _u('/api/art/${Uri.encodeComponent(name)}'),
          headers: {'X-Admin-Key': adminKey},
        )
        .timeout(const Duration(seconds: 15));
    if (r.statusCode >= 300) {
      Map<String, dynamic> data = {};
      try {
        data = jsonDecode(r.body) as Map<String, dynamic>;
      } catch (_) {}
      final detail = data['detail'];
      throw detail is String && detail.isNotEmpty
          ? detail
          : 'Falha ao apagar a imagem (HTTP ${r.statusCode}).';
    }
  }

  static Future<Map<String, dynamic>> opening(int sessionId) async {
    final r = await http
        .post(_u('/api/sessions/$sessionId/opening'))
        .timeout(const Duration(minutes: 6));
    Map<String, dynamic> d;
    try {
      d = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      d = {};
    }
    if (r.statusCode >= 300) {
      final detail = d['detail'];
      throw detail is String && detail.isNotEmpty
          ? detail
          : 'Falha ao abrir a cena (HTTP ${r.statusCode}).';
    }
    return d;
  }

  static Future<List<ChatMessage>> messages(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/messages'))
        .timeout(const Duration(seconds: 10));
    return (jsonDecode(r.body) as List)
        .map((e) => ChatMessage.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<Map<String, dynamic>> threads(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/threads'))
        .timeout(const Duration(seconds: 15));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao carregar o diário.');
    }
    return data;
  }

  static Future<List<ChatMessage>> searchMessages(
    int sessionId,
    String query,
  ) async {
    final r = await http
        .get(
          _u(
            '/api/sessions/$sessionId/search'
            '?q=${Uri.encodeQueryComponent(query)}',
          ),
        )
        .timeout(const Duration(seconds: 20));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao buscar na história.');
    }
    return ((data['results'] as List?) ?? const [])
        .map((e) => ChatMessage.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<Map<String, dynamic>> sessionTree(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/tree'))
        .timeout(const Duration(seconds: 15));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao carregar as ramificações.');
    }
    return data;
  }

  static Future<Map<String, dynamic>> codex(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/codex'))
        .timeout(const Duration(seconds: 20));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao carregar o código.');
    }
    return data;
  }

  static Future<Map<String, dynamic>> director(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/director'))
        .timeout(const Duration(seconds: 20));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao abrir o painel do diretor.');
    }
    return data;
  }

  static Future<Map<String, dynamic>> digest(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/digest'))
        .timeout(const Duration(seconds: 15));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao carregar o mundo.');
    }
    return data;
  }

  static Future<Map<String, dynamic>> chronicle(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/chronicle'))
        .timeout(const Duration(seconds: 30));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao gerar a crônica.');
    }
    return data;
  }

  static Future<void> updateMessage(
    int sessionId,
    int messageId,
    String content,
  ) async {
    final r = await http
        .put(
          _u('/api/sessions/$sessionId/messages/$messageId'),
          headers: _json,
          body: jsonEncode({'content': content}),
        )
        .timeout(const Duration(seconds: 10));
    if (r.statusCode >= 300) throw 'HTTP ${r.statusCode}';
  }

  static Future<void> deleteMessage(int sessionId, int messageId) async {
    final r = await http
        .delete(_u('/api/sessions/$sessionId/messages/$messageId'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode >= 300) throw 'HTTP ${r.statusCode}';
  }

  static Future<void> truncateAfter(int sessionId, int messageId) async {
    final r = await http
        .post(_u('/api/sessions/$sessionId/messages/$messageId/truncate'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode >= 300) throw 'HTTP ${r.statusCode}';
  }

  static Future<List<Choice>> choices(int sessionId) async {
    final r = await http
        .post(_u('/api/sessions/$sessionId/choices'))
        .timeout(const Duration(seconds: 180));
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    return ((d['choices'] as List?) ?? const [])
        .map(Choice.fromJson)
        .where((choice) => choice.text.trim().isNotEmpty)
        .toList();
  }

  static Future<Session> branchSession(int sessionId, {int? messageId}) async {
    final r = await http
        .post(
          _u('/api/sessions/$sessionId/branch'),
          headers: _json,
          body: jsonEncode({'message_id': messageId}),
        )
        .timeout(const Duration(seconds: 20));
    return Session.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  static Future<Map<String, dynamic>> rewindSession(
    int sessionId,
    int messageId,
  ) async {
    final r = await http
        .post(
          _u('/api/sessions/$sessionId/rewind'),
          headers: _json,
          body: jsonEncode({'message_id': messageId}),
        )
        .timeout(const Duration(seconds: 20));
    if (r.statusCode >= 300) throw 'HTTP ${r.statusCode}';
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<Uint8List> tts(String text, {String voice = ''}) async {
    final r = await http
        .post(
          _u('/api/tts'),
          headers: _json,
          body: jsonEncode({'text': text, 'voice': voice}),
        )
        .timeout(const Duration(seconds: 120));
    if (r.statusCode != 200) throw 'tts HTTP ${r.statusCode}';
    return r.bodyBytes;
  }

  static Future<Map<String, dynamic>> chapterStatus(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/chapter_status'))
        .timeout(const Duration(seconds: 10));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<List<Chapter>> chapters(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/chapters'))
        .timeout(const Duration(seconds: 15));
    return ((jsonDecode(r.body) as List?) ?? const [])
        .map((e) => Chapter.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<Map<String, dynamic>> createChapter(int sessionId) async {
    final r = await http
        .post(
          _u('/api/sessions/$sessionId/chapters'),
          headers: _json,
          body: jsonEncode({}),
        )
        .timeout(const Duration(seconds: 300));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<List<Map<String, dynamic>>> worldFacts(int worldId) async {
    final r = await http
        .get(_u('/api/worlds/$worldId/facts'))
        .timeout(const Duration(seconds: 10));
    return (jsonDecode(r.body) as List)
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
  }

  static Future<Map<String, dynamic>> addWorldFact(
    int worldId,
    String text,
    String keys,
  ) async {
    final r = await http
        .post(
          _u('/api/worlds/$worldId/facts'),
          headers: _json,
          body: jsonEncode({'text': text, 'keys': keys}),
        )
        .timeout(const Duration(seconds: 15));
    return (jsonDecode(r.body) as Map).cast<String, dynamic>();
  }

  static Future<void> deleteFact(int id) async {
    await http
        .delete(_u('/api/facts/$id'))
        .timeout(const Duration(seconds: 10));
  }

  static Future<Map<String, dynamic>> memory(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/memory'))
        .timeout(const Duration(seconds: 15));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<Map<String, dynamic>> relationship(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/relationship'))
        .timeout(const Duration(seconds: 10));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<Map<String, dynamic>> sheet(int sessionId) async {
    final r = await http
        .get(_u('/api/sessions/$sessionId/sheet'))
        .timeout(const Duration(seconds: 15));
    Map<String, dynamic> data;
    try {
      data = jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      data = {};
    }
    if (r.statusCode >= 300) {
      throw _detail(data, r.statusCode, 'Falha ao carregar a ficha.');
    }
    return data;
  }

  static Future<void> resetSession(int sessionId) async {
    await http
        .post(_u('/api/sessions/$sessionId/reset'))
        .timeout(const Duration(seconds: 10));
  }

  static Stream<ChatChunk> chatStream({
    required int sessionId,
    String message = '',
    String model = '',
    bool ooc = false,
    bool continueScene = false,
    bool regen = false,
    String requestId = '',
  }) async* {
    final req = http.Request('POST', _u('/api/sessions/$sessionId/chat'));
    req.headers['Content-Type'] = 'application/json';
    req.body = jsonEncode({
      'message': message,
      'model': model,
      'ooc': ooc,
      'continue_scene': continueScene,
      'regen': regen,
      'request_id': requestId,
    });
    final client = http.Client();
    try {
      final res = await client.send(req).timeout(const Duration(seconds: 30));
      if (res.statusCode != 200) {
        yield ChatChunk(delta: '\n[erro HTTP ${res.statusCode}]');
        return;
      }
      final lines = res.stream
          .transform(utf8.decoder)
          .transform(const LineSplitter());
      await for (final line in lines) {
        final t = line.trim();
        if (t.isEmpty) continue;
        Map<String, dynamic> o;
        try {
          o = jsonDecode(t) as Map<String, dynamic>;
        } catch (_) {
          continue;
        }
        if (o['status'] != null) {
          yield ChatChunk(status: o['status'] as String);
        }
        if (o['delta'] != null) yield ChatChunk(delta: o['delta'] as String);
        if (o['error'] != null) {
          yield ChatChunk(delta: '\n[erro: ${o['error']}]', status: 'error');
        }
        if (o['replace'] != null) {
          yield ChatChunk(replace: o['replace'] as String);
        }
        final resolution = _asJsonMap(o['resolution']);
        if (resolution != null && o['done'] != true) {
          yield ChatChunk(resolution: resolution);
        }
        if (o['done'] == true) {
          final rawAxes = (o['axes'] as Map?) ?? const {};
          yield ChatChunk(
            done: true,
            nr: (o['nr'] as num?)?.toInt(),
            axes: rawAxes.map(
              (k, v) => MapEntry(k.toString(), (v as num).toInt()),
            ),
            vinculo: (o['vinculo'] ?? '').toString(),
            scene: _asJsonMap(o['scene']),
            pacing: _asJsonMap(o['pacing']),
            commitments: _asJsonMap(o['commitments']),
            cast: _asJsonMapList(o['cast']),
            resolution: resolution,
            mood: (o['mood'] ?? '').toString(),
          );
          break;
        }
        if (o['nr'] != null) {
          final raw = (o['axes'] as Map?) ?? const {};
          yield ChatChunk(
            nr: (o['nr'] as num).toInt(),
            axes: raw.map((k, v) => MapEntry(k.toString(), (v as num).toInt())),
            vinculo: (o['vinculo'] ?? '').toString(),
          );
        }
      }
    } finally {
      client.close();
    }
  }
}
