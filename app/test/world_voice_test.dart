import 'package:flutter_test/flutter_test.dart';
import 'package:luminus/api.dart';

void main() {
  test('WorldVoice decodifica claims e known_by', () {
    final voice = WorldVoice.fromJson({
      'id': 7,
      'session_id': 3,
      'turn': 12,
      'kind': 'carta',
      'title': 'Carta sem brasão',
      'author': 'um aliado distante',
      'body': 'Ao meu caro aliado...',
      'claims': ['a ponte caiu'],
      'truth_status': 'distorcido',
      'known_by': ['um aliado distante', 'Lia'],
      'visibility': 'restrito',
      'source': 'offscreen',
      'created_at': 1760000000.5,
    });

    expect(voice.id, 7);
    expect(voice.sessionId, 3);
    expect(voice.kind, 'carta');
    expect(voice.claims, ['a ponte caiu']);
    expect(voice.truthStatus, 'distorcido');
    expect(voice.knownBy, ['um aliado distante', 'Lia']);
    expect(voice.visibility, 'restrito');
  });

  test('WorldVoice tolera campos ausentes', () {
    final voice = WorldVoice.fromJson({'id': 1});

    expect(voice.id, 1);
    expect(voice.kind, 'rumor');
    expect(voice.claims, isEmpty);
    expect(voice.knownBy, isEmpty);
    expect(voice.truthStatus, 'incerto');
  });
}
