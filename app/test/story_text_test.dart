import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:luminus/story_text.dart';
import 'package:luminus/theme.dart';

void main() {
  testWidgets('remove marcadores e mantém o texto', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildLuminusTheme(),
        home: const Scaffold(
          body: StoryText(
            text: 'Ela *sussurra* algo e **grita** depois.',
            style: TextStyle(),
          ),
        ),
      ),
    );

    final rich = tester.widget<RichText>(find.byType(RichText));
    expect(
      rich.text.toPlainText(),
      'Ela sussurra algo e grita depois.',
    );
  });

  testWidgets('mostra o cursor durante o streaming', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildLuminusTheme(),
        home: const Scaffold(
          body: StoryText(
            text: 'A porta abre',
            style: TextStyle(),
            streaming: true,
          ),
        ),
      ),
    );

    final rich = tester.widget<RichText>(find.byType(RichText));
    expect(rich.text.toPlainText(), 'A porta abre▍');
  });
}
