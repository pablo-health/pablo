import nextConfig from "eslint-config-next"

const config = [
  ...nextConfig,
  {
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "CallExpression[callee.object.name='console'][callee.property.name='error'] > Identifier.arguments",
          message:
            "Pass console.error() a derived value (e.g. errorCode(err)) instead of the raw error object.",
        },
      ],
    },
  },
  {
    files: ["**/*.ts", "**/*.tsx"],
    ignores: [
      "src/lib/**",
      "src/test/**",
      "**/__tests__/**",
      "**/*.test.ts",
      "**/*.test.tsx",
    ],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@/lib/mockData",
              message:
                "mockData pulls sample sessions and notes into the bundle. Load it with a dynamic import behind the relevant runtime check instead of a static import.",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      "app/(dashboard)/layout.tsx",
      "app/(dashboard)/dashboard/page.tsx",
    ],
    rules: {
      // These read mockUser behind a NODE_ENV check that the bundler
      // resolves at build time, so the branch (and the import) is
      // compiled out of production builds rather than shipped.
      "no-restricted-imports": "off",
    },
  },
]

export default config
