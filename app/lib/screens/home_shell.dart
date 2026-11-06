import 'package:flutter/material.dart';

import '../theme.dart';
import 'characters_screen.dart';
import 'home_screen.dart';
import 'image_studio_screen.dart';
import 'settings_screen.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;
  final _homeKey = GlobalKey<HomeScreenState>();

  void _select(int index) {
    if (index == _index) return;
    setState(() => _index = index);
    if (index == 0) _homeKey.currentState?.refresh();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: kBg,
      body: IndexedStack(
        index: _index,
        children: [
          HomeScreen(key: _homeKey, onExplore: () => _select(1)),
          const CharactersScreen(),
          const ImageStudioScreen(),
          const SettingsScreen(),
        ],
      ),
      bottomNavigationBar: DecoratedBox(
        decoration: const BoxDecoration(
          border: Border(top: BorderSide(color: kBorder)),
        ),
        child: NavigationBar(
          selectedIndex: _index,
          onDestinationSelected: _select,
          backgroundColor: kSurface,
          surfaceTintColor: Colors.transparent,
          indicatorColor: kPrimary.withValues(alpha: .2),
          elevation: 0,
          height: 68,
          labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
          destinations: const [
            NavigationDestination(
              icon: Icon(Icons.auto_stories_outlined),
              selectedIcon: Icon(Icons.auto_stories, color: kAccent),
              label: 'Início',
            ),
            NavigationDestination(
              icon: Icon(Icons.people_outline),
              selectedIcon: Icon(Icons.people, color: kAccent),
              label: 'Personagens',
            ),
            NavigationDestination(
              icon: Icon(Icons.palette_outlined),
              selectedIcon: Icon(Icons.palette, color: kAccent),
              label: 'Estúdio',
            ),
            NavigationDestination(
              icon: Icon(Icons.settings_outlined),
              selectedIcon: Icon(Icons.settings, color: kAccent),
              label: 'Ajustes',
            ),
          ],
        ),
      ),
    );
  }
}
