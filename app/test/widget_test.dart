import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:luminus/theme.dart';

void main() {
  testWidgets('Luminus background renders', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildLuminusTheme(),
        home: const Scaffold(
          body: LuminusBackground(child: SizedBox.expand()),
        ),
      ),
    );
    expect(find.byType(LuminusBackground), findsOneWidget);
  });
}
