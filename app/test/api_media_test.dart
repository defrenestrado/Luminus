import 'package:flutter_test/flutter_test.dart';
import 'package:luminus/api.dart';

void main() {
  test('resolve caminhos de arte no servidor atual', () {
    ApiClient.baseUrl = 'http://192.168.1.9:5111';
    expect(
      ApiClient.mediaUrl('/art/cover.jpg'),
      'http://192.168.1.9:5111/art/cover.jpg',
    );
    expect(
      ApiClient.mediaUrl('https://cdn.example/x.jpg'),
      'https://cdn.example/x.jpg',
    );
    expect(ApiClient.mediaUrl(''), '');
  });
}
