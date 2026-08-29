# Nexus Authoritative Connector Register

**Author:** Manus AI  
**Scope:** SEC Game Day mobility in Auburn, Alabama

Nexus must never silently replace unavailable authoritative data with synthetic records. Each connector is registered with an owner, permitted use, expected cadence, stale threshold, schema version, and explicit health state.

| Domain | Authoritative source | Machine-readable status | Initial Nexus treatment |
|---|---|---|---|
| Traffic counts and assets | City of Auburn Transportation ArcGIS MapServer | Public ArcGIS REST service; supports JSON/GeoJSON/PBF queries. Layer 1 is Average Daily Traffic, layer 10 traffic signals, layers 20–21 parking geometry.[1] | Connect now for traffic-count calibration and authoritative transportation geometry. Treat ADT as historical context, not live speed. |
| State traffic context | ALDOT Traffic Data and ALGO Traffic | ALDOT publishes public traffic-count maps. The official ALGO traveler map at [algotraffic.com/map](https://algotraffic.com/map) loads public JSON for traffic events, I-85 travel times, and message signs.[2] [3] | Ingest Auburn-box events, Auburn I-85 travel times, and Auburn message-sign text from that traveler JSON. Do not ingest camera imagery or ALEA missing-person alerts. Keep a formal TMC/ALGO agreement as a later upgrade. |
| University parking | Auburn University Parking Services / AU Parking App / FoPark | Auburn states that its parking app provides real-time availability. The officially linked AU Parking site says FoPark/Focus Engineering processes camera streams and reports open and occupied spaces in near real time.[4] [9] | Register occupancy as partner-gated. Do not scrape the app or camera-derived detail. Request an approved lot-level capacity/occupancy interface from Auburn Parking Services and FoPark. Public City parking geometry may still be connected separately. |
| Tiger Transit | Auburn University ETA Spot | Auburn states that ETA Spot provides real-time vehicle locations and all Tiger Transit buses carry GPS.[5] [6] The official public deployment calls `service.php` endpoints for vehicles, routes, patterns, stops, version, detours, and ETAs. `get_vehicles` returns live latitude, longitude, route, in-service status, receive time, schedule adherence, capacity, and next-stop fields; route and stop endpoints expose official identifiers and game-day stops. | Implement a read-only ETA Spot connector for vehicle, route, stop, and service-status observations. The public web client exposes `token=TESTING`; confirm permitted server-side production use and cadence with Auburn Transportation Services/ETA Transit before the pilot. |
| Road closures | City of Auburn `RoadClosuresPublic` FeatureServer | City describes the map as live and future road closures, blocks, and detours.[7] The public, read-only City feature service is `https://ocean.auburnalabama.org/arcgis/rest/services/Hosted/RoadClosuresPublic/FeatureServer` with Blocks (0), Closures (1), and Detours (2).[10] | Connect now with ArcGIS query operations and `f=geojson`. If the service becomes unavailable, show `unavailable`; never infer a closure. |
| Emergency access | Auburn Athletics game-day rules and agency operational plans | Public game-day guidance requires streets and emergency lanes to remain open except law-enforcement barricades.[8] Real corridor status is not publicly machine-readable. | Register a partner-gated emergency-access connector owned by Public Safety/Event Command. Until authorized, display `not connected`, not a simulated corridor status. |

## References

[1]: https://gisportal.auburnalabama.org/server/rest/services/Transportation/Transportation/MapServer?f=pjson "City of Auburn Transportation MapServer"
[2]: https://aldotgis.dot.state.al.us/TDMPublic/ "ALDOT Public Traffic Data"
[3]: https://www.dot.state.al.us/travel.html "ALDOT Travel Information"
[4]: https://www.auburn.edu/parking "Auburn University Parking Services"
[5]: https://auburn.edu/administration/transit/etaspot/ "Auburn University ETA Transit Information"
[6]: https://auburn.edu/administration/transit/routes/ "Auburn University Tiger Transit Routes"
[7]: https://www.auburnal.gov/maps/ "City of Auburn Maps"
[8]: https://auburntigers.com/fb-gameday/parking-and-tailgating "Auburn Football Gameday Parking and Traffic"
[9]: https://au-parking.com/ "AU Parking / FoPark"
[10]: https://ocean.auburnalabama.org/arcgis/rest/services/Hosted/RoadClosuresPublic/FeatureServer?f=pjson "City of Auburn RoadClosuresPublic FeatureServer"
