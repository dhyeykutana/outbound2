/*
Template Name: Skotwind - Responsive 5 Admin Dashboard
Author: MyraStudio
File: vector map js
*/

// If using jsvectormap via NPM
import 'jsvectormap';
import 'jsvectormap/dist/jsvectormap.min.css';

import 'jsvectormap/dist/maps/world-merc.js';
import 'jsvectormap/dist/maps/world.js';

import '../maps/in-mill-en.js';
import '../maps/canada.js';
import '../maps/russia.js';
import '../maps/iraq.js';
import '../maps/spain.js';



class VectorMap {

    initWorldMapMarker() {
        const map = new jsVectorMap({
            map: 'world',
            selector: '#world-map-markers',
            zoomOnScroll: false,
            zoomButtons: true,
            markersSelectable: true,
            markers: [
                { name: "Greenland", coords: [72, -42] },
                { name: "Canada", coords: [56.1304, -106.3468] },
                { name: "Brazil", coords: [-14.2350, -51.9253] },
                { name: "Egypt", coords: [26.8206, 30.8025] },
                { name: "Russia", coords: [61, 105] },
                { name: "China", coords: [35.8617, 104.1954] },
                { name: "United States", coords: [37.0902, -95.7129] },
                { name: "Norway", coords: [60.472024, 8.468946] },
                { name: "Ukraine", coords: [48.379433, 31.16558] },
            ],
            markerStyle: {
                initial: { fill: "#5B8DEC" },
                selected: { fill: "#ef5f5f" }
            },
            labels: {
                markers: {
                    render: marker => marker.name
                }
            }
        });
    }

    initIndiaVectorMap() {
        const map = new jsVectorMap({
            map: 'in_mill',
            selector: '#india-vector-map',
            regionStyle: {
                initial: {
                    fill: '#FF671F'
                }
            }
        });
    }

    initCanadaVectorMap() {
        const map = new jsVectorMap({
            map: 'canada',
            selector: '#canada-vector-map',
            zoomOnScroll: false,
            regionStyle: {
                initial: {
                    fill: '#5B8DEC'
                }
            }
        });
    }

    initRussiaVectorMap() {
        const map = new jsVectorMap({
            map: 'russia',
            selector: '#russia-vector-map',
            zoomOnScroll: false,
            regionStyle: {
                initial: {
                    fill: '#5d7186'
                }
            }
        });
    }


    initIraqVectorMap() {
        const map = new jsVectorMap({
            map: 'iraq',
            selector: '#iraq-vector-map',
            zoomOnScroll: false,
            regionStyle: {
                initial: {
                    fill: '#20c8e9'
                }
            }
        });
    }

    initSpainVectorMap() {
        const map = new jsVectorMap({
            map: 'spain',
            selector: '#spain-vector-map',
            zoomOnScroll: false,
            regionStyle: {
                initial: {
                    fill: '#ffe381'
                }
            }
        });
    }



    init() {
        this.initWorldMapMarker();
        this.initIndiaVectorMap();
        this.initCanadaVectorMap();
        this.initRussiaVectorMap();
        this.initIraqVectorMap();
        this.initSpainVectorMap();
    }

}

document.addEventListener('DOMContentLoaded', function (e) {
    new VectorMap().init();
});