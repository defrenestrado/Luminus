class AppUpdater {
  static const int buildNumber = int.fromEnvironment(
    'LUMINUS_BUILD_NUMBER',
    defaultValue: 0,
  );

  static Future<Map<String, dynamic>?> check() async => null;

  static Future<void> downloadAndInstall({
    void Function(double progress)? onProgress,
    void Function(String status)? onStatus,
  }) async {
    throw 'Atualização de APK só está disponível no app Android.';
  }
}
