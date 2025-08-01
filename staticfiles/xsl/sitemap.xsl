<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" 
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform" 
                xmlns:s="http://www.sitemaps.org/schemas/sitemap/0.9">
    <xsl:output method="html" version="1.0" encoding="UTF-8" indent="yes"/>
    
    <xsl:template match="/">
        <html>
            <head>
                <title>XML Sitemap</title>
                <style type="text/css">
                    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; color: #333; }
                    #container { max-width: 900px; margin: 24px auto; }
                    table { width: 100%; border-collapse: collapse; }
                    th, td { padding: 12px 15px; text-align: left; }
                    thead th { background-color: #2c3e50; color: #ecf0f1; border-bottom: 2px solid #34495e; }
                    tbody tr:nth-child(even) { background-color: #f8f9fa; }
                    tbody tr:hover { background-color: #e9ecef; }
                    a { color: #3498db; text-decoration: none; }
                    a:hover { text-decoration: underline; }
                    h1 { color: #2c3e50; border-bottom: 1px solid #bdc3c7; padding-bottom: 10px; }
                    footer { margin-top: 24px; font-size: 0.9em; color: #7f8c8d; text-align: center; }
                </style>
            </head>
            <body>
                <div id="container">
                    <h1>XML Sitemap</h1>
                    <p>This is an XML sitemap, intended for consumption by search engines. For more information about XML sitemaps, please see <a href="http://sitemaps.org">sitemaps.org</a>.</p>
                    
                    <xsl:if test="count(s:sitemapindex/s:sitemap) &gt; 0">
                        <h2>Sitemap Index</h2>
                        <p>This sitemap contains the following sitemaps:</p>
                        <table>
                            <thead>
                                <tr>
                                    <th>Sitemap URL</th>
                                    <th>Last Modified</th>
                                </tr>
                            </thead>
                            <tbody>
                                <xsl:for-each select="s:sitemapindex/s:sitemap">
                                    <tr>
                                        <td>
                                            <a href="{s:loc}"><xsl:value-of select="s:loc"/></a>
                                        </td>
                                        <td>
                                            <xsl:value-of select="concat(substring(s:lastmod,9,2), '/', substring(s:lastmod,6,2), '/', substring(s:lastmod,1,4))"/>
                                        </td>
                                    </tr>
                                </xsl:for-each>
                            </tbody>
                        </table>
                    </xsl:if>
                    
                    <xsl:if test="count(s:urlset/s:url) &gt; 0">
                        <h2>URL List</h2>
                        <p>This sitemap contains the following URLs:</p>
                        <table>
                            <thead>
                                <tr>
                                    <th>URL</th>
                                    <th>Last Modified</th>
                                    <th>Change Freq.</th>
                                    <th>Priority</th>
                                </tr>
                            </thead>
                            <tbody>
                                <xsl:for-each select="s:urlset/s:url">
                                    <tr>
                                        <td>
                                            <a href="{s:loc}"><xsl:value-of select="s:loc"/></a>
                                        </td>
                                        <td>
                                             <xsl:value-of select="concat(substring(s:lastmod,9,2), '/', substring(s:lastmod,6,2), '/', substring(s:lastmod,1,4))"/>
                                        </td>
                                        <td><xsl:value-of select="s:changefreq"/></td>
                                        <td><xsl:value-of select="s:priority"/></td>
                                    </tr>
                                </xsl:for-each>
                            </tbody>
                        </table>
                    </xsl:if>
                    <footer>Generated by a helpful AI.</footer>
                </div>
            </body>
        </html>
    </xsl:template>
</xsl:stylesheet>