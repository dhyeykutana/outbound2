import { sync } from 'glob';
import { defineConfig } from 'vite'
import path, { resolve } from "path";
import handlebars from "vite-plugin-handlebars";
import tailwindcss from '@tailwindcss/vite'


export default defineConfig(({ command, mode, ssrBuild }) => {
    const list = [];
    if (mode === 'production') {
        sync('src/*.html').forEach((file) => {
            list.push(file);
        })
    }
    return {
        root: "src",
        base: "/",
        server: {
            open: true
        },
        plugins: [
            tailwindcss(),
            handlebars({
                partialDirectory: resolve('./src/partials'),
            }),
        ],
        resolve: {
            alias: {
                "@css": path.resolve("./src/assets/css/"),
                "@/*": path.resolve("./*"),
            },
        },
        build: {
            outDir: "../dist",
            rollupOptions: {
                input: [
                    // 'src/index.html',
                    ...list,

                ],
            }
        }
    }
})
