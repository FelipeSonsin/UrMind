/**
 * Textos dos botões e avisos do mapa (MapLibre) em português. Sem esta tabela, o mapa
 * mostra as dicas em inglês ("Zoom in", "Find my location") ao passar o mouse e nos
 * leitores de tela. A chave é o identificador do MapLibre (src/ui/default_locale).
 */
export const MAP_LOCALE: Record<string, string> = {
  'AttributionControl.MapFeedback': 'Sugerir correção no mapa',
  'AttributionControl.ToggleAttribution': 'Mostrar ou ocultar os créditos do mapa',
  'CooperativeGesturesHandler.MacHelpText': 'Use ⌘ + rolagem para aproximar o mapa',
  'CooperativeGesturesHandler.MobileHelpText': 'Use dois dedos para mover o mapa',
  'CooperativeGesturesHandler.WindowsHelpText': 'Use Ctrl + rolagem para aproximar o mapa',
  'FullscreenControl.Enter': 'Tela cheia',
  'FullscreenControl.Exit': 'Sair da tela cheia',
  'GeolocateControl.FindMyLocation': 'Mostrar onde estou',
  'GeolocateControl.LocationNotAvailable': 'Localização indisponível',
  'GlobeControl.Disable': 'Ver mapa plano',
  'GlobeControl.Enable': 'Ver globo',
  'LogoControl.Title': 'Logo do MapLibre',
  'Map.Title': 'Mapa',
  'NavigationControl.ResetBearing': 'Arraste para girar o mapa; clique para voltar ao norte',
  'NavigationControl.ZoomIn': 'Aproximar',
  'NavigationControl.ZoomOut': 'Afastar',
  'Popup.Close': 'Fechar',
  'TerrainControl.Disable': 'Desligar relevo',
  'TerrainControl.Enable': 'Ligar relevo',
};
