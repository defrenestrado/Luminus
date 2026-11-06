import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:luminus/screens/character_form_screen.dart';
import 'package:luminus/theme.dart';

void main() {
  testWidgets('abre na primeira etapa e valida o nome', (tester) async {
    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildLuminusTheme(),
        home: const CharacterFormScreen(kind: 'npc'),
      ),
    );
    await tester.pump();

    expect(find.text('Etapa 1 de 3 · Identidade'), findsOneWidget);
    expect(find.text('Identidade'), findsOneWidget);

    await tester.tap(find.text('Continuar'));
    await tester.pumpAndSettle();

    expect(
      find.text('Dê um nome ao personagem antes de continuar.'),
      findsOneWidget,
    );
  });

  testWidgets('protagonista tem quatro atributos na etapa Alma', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildLuminusTheme(),
        home: const CharacterFormScreen(kind: 'protagonist'),
      ),
    );
    await tester.pump();

    await tester.enterText(find.widgetWithText(TextField, 'Nome'), 'Aria');
    await tester.tap(find.text('Continuar'));
    await tester.pumpAndSettle();

    expect(find.text('ATRIBUTOS'), findsOneWidget);
    expect(find.byType(Slider), findsNWidgets(4));
    expect(find.text('Corpo'), findsOneWidget);
    expect(find.text('Sorte'), findsOneWidget);

    await tester.drag(find.byType(ListView), const Offset(0, -420));
    await tester.pumpAndSettle();
    expect(find.text('PERÍCIAS · 0/10'), findsOneWidget);
    await tester.tap(find.byTooltip('Aumentar Luta'));
    await tester.pump();
    expect(find.text('PERÍCIAS · 1/10'), findsOneWidget);
  });
}
